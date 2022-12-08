import logging
import carla
import random
import math, time
import numpy as np
import time
from enum import Enum
from queue import Queue
#from gym_carla.env.agent.basic_agent import BasicAgent
from gym_carla.env.util.misc import draw_waypoints, get_speed, get_acceleration, test_waypoint, \
    compute_distance, get_actor_polygons, get_lane_center, remove_unnecessary_objects,get_yaw_diff
from gym_carla.env.sensor import CollisionSensor, LaneInvasionSensor, SemanticTags
from gym_carla.env.agent.route_planner import GlobalPlanner, LocalPlanner
from gym_carla.env.agent.pid_controller import VehiclePIDController
from gym_carla.env.carla.behavior_agent import BehaviorAgent, BasicAgent
from gym_carla.env.carla.basic_lanechanging_agent import Basic_Lanechanging_Agent
from collections import deque
FOLLOW = 0
CHANGE_LEFT = -1
CHANGE_RIGHT = 1

class SpeedState(Enum):
    """Different ego vehicle speed state
        START: Initializing state, speed up the vehicle to speed_threshole, use basic agent controller
        RUNNING: After initializing, ego speed between speed_min and speed_limit, use RL controller
        REBOOT: After initializaing, ego speed reaches below speed min, use basic agent controller to speed up ego vehicle to speed_threshold
    """
    START = 0
    RUNNING = 1
    RUNNING_RL = 2
    RUNNING_PID = 3
    REBOOT = 4


class CarlaEnv:
    def __init__(self, args) -> None:
        super().__init__()
        self.host = args.host
        self.port = args.port
        self.tm_port = args.tm_port
        self.sync = args.sync
        self.fps = args.fps
        self.no_rendering = args.no_rendering
        self.ego_filter = args.filter
        self.loop = args.loop
        self.agent = args.agent
        self.behavior = args.behavior
        self.res = args.res
        self.num_of_vehicles = args.num_of_vehicles
        self.sampling_resolution = args.sampling_resolution
        self.min_distance = args.min_distance
        self.vehicle_proximity = args.vehicle_proximity
        self.hybrid = args.hybrid
        self.auto_lanechange = args.auto_lane_change
        self.guide_change = args.guide_change
        self.stride = args.stride
        self.buffer_size = args.buffer_size
        self.pre_train_steps = args.pre_train_steps
        self.speed_limit = args.speed_limit
        self.lane_change_reward = args.lane_change_reward
        # The RL agent acts only after ego vehicle speed reach speed threshold
        self.speed_threshold = args.speed_threshold
        self.speed_min = args.speed_min
        # controller action space
        self.throttle_brake = 0
        self.steer_bound = args.steer_bound
        self.throttle_bound = args.throttle_bound
        self.brake_bound = args.brake_bound

        logging.info('listening to server %s:%s', args.host, args.port)
        self.client = carla.Client(self.host, self.port)
        self.client.set_timeout(10.0)
        self.world = self.client.load_world(args.map)
        remove_unnecessary_objects(self.world)
        self.map = self.world.get_map()
        self.origin_settings = self.world.get_settings()
        self.traffic_manager = None
        self.speed_state = SpeedState.START
        self._set_traffic_manager()
        logging.info('Carla server connected')

        # Record the time of total steps
        self.reset_step = 0
        self.total_step = 0
        self.time_step = 0
        self.rl_control_step = 0
        self.rl_control_episode = 0
        self.tm_control_episode = 0
        # Let the RL controller and PID controller alternatively take control every 500 steps
        # RL_switch: True--currently RL in control, False--currently PID in control
        # TM_switch: True--currently traffic manager in control, False--currently Basic Agent in control
        self.RL_switch = False
        self.TM_switch = False
        self.SWITCH_THRESHOLD = args.switch_threshold
        self.next_wps = None  # ego vehicle's following waypoint list
        self.left_wps = None
        self.center_wps = None
        self.right_wps = None
        self.left_rear_wps = None
        self.center_rear_wps = None
        self.right_rear_wps = None
        self.state_waypoints = None
        self.draw_waypoints = None

        self.current_lane = None
        self.target_lane = None
        self.last_action = None
        self.new_target_lane = None
        self.new_action = None

        self.distance_to_front_vehicles = [50, 50, 50]
        self.distance_to_rear_vehicles = [50, 50, 50]

        self.calculate_impact = None

        # generate ego vehicle spawn points on chosen route
        self.global_planner = GlobalPlanner(self.map, self.sampling_resolution)
        self.local_planner = None
        self.spawn_points = self.global_planner.get_spawn_points()
        # for p in self.spawn_points:
        #     print(p.lane_id)
        self.ego_spawn_point = None
        # former_wp record the ego vehicle waypoint of former step

        # arguments for debug
        self.debug = args.debug
        self.train = args.train  # argument indicating training agent
        self.seed = args.seed
        self.former_wp = None

        # arguments for caculating reward
        self.TTC_THRESHOLD = args.TTC_th
        self.penalty = args.penalty
        self.last_acc = carla.Vector3D()  # ego vehicle acceration in last step
        self.rear_vel_deque = deque(maxlen=2)
        self.step_info = None

        if self.debug:
            # draw_waypoints(self.world,self.global_panner.get_route())
            random.seed(self.seed)

        # Set fixed simulation step for synchronous mode
        self._set_synchronous_mode()

        # Set weather
        # self.world.set_weather(carla.WeatherParamertes.ClearNoon)

        self.companion_vehicles = []
        self.ego_vehicle = None
        # the vehicle in front of ego vehicle
        self.vehicle_front = None
        self.vehicle_rear = None
        self.vehicle_inlane = None

        # Collision sensor
        self.collision_sensor = None
        self.lane_invasion_sensor = None

        # thread blocker
        self.sensor_queue = Queue(maxsize=10)
        self.camera = None

    def __del__(self):
        logging.info('\n Destroying all vehicles')
        self.world.apply_settings(self.origin_settings)
        self._clear_actors(['vehicle.*', 'sensor.other.collison', 'sensor.camera.rgb', 'sensor.other.lane_invasion'])

    def reset(self):
        if self.ego_vehicle is not None:
            # self.world.apply_settings(self.origin_settings)
            # self._set_synchronous_mode()
            self._clear_actors(
                ['*vehicle.*', 'sensor.other.collison', 'sensor.camera.rgb', 'sensor.other.lane_invasion'])
            self.ego_vehicle = None
            self.companion_vehicles = []
            self.collision_sensor = None
            self.lane_invasion_sensor = None
            self.camera = None
            while (self.sensor_queue.empty() is False):
                self.sensor_queue.get(block=False)

        # Spawn surrounding vehicles
        self._spawn_companion_vehicles()
        self.calculate_impact = False
        self.rear_vel_deque.append(-1)
        self.rear_vel_deque.append(-1)
        # Get actors polygon list
        self.vehicle_polygons = []
        vehicle_poly_dict = get_actor_polygons(self.world, 'vehicle.*')
        self.vehicle_polygons.append(vehicle_poly_dict)

        # try to spawn ego vehicle
        while self.ego_vehicle is None:
            self.ego_spawn_point = random.choice(self.spawn_points)
            # print('1')
            self.former_wp = get_lane_center(self.map, self.ego_spawn_point.location)
            self.ego_vehicle = self._try_spawn_ego_vehicle_at(self.ego_spawn_point)
        # self.ego_vehicle.set_simulate_physics(False)
        self.collision_sensor = CollisionSensor(self.ego_vehicle)
        self.lane_invasion_sensor = LaneInvasionSensor(self.ego_vehicle)
        self.throttle_brake = 0.0
        # print('2', self.ego_vehicle)
        self.last_action = FOLLOW
        self.new_action = FOLLOW
        # friction_bp=self.world.get_blueprint_library().find('static.trigger.friction')
        # bb_extent=self.ego_vehicle.bounding_box.extent
        # friction_bp.set_attribute('friction',str(0.0))
        # friction_bp.set_attribute('extent_x',str(bb_extent.x))
        # friction_bp.set_attribute('extent_y',str(bb_extent.y))
        # friction_bp.set_attribute('extent_z',str(bb_extent.z))
        # self.world.spawn_actor(friction_bp,self.ego_vehicle.get_transform())
        # self.world.debug.draw_box()

        # let the client interact with server
        if self.sync:
            self.world.tick()

            spectator = self.world.get_spectator()
            transform = self.ego_vehicle.get_transform()
            spectator.set_transform(carla.Transform(transform.location + carla.Location(z=100),
                                                    carla.Rotation(pitch=-90)))
        else:
            self.world.wait_for_tick()

        """Attention:
        get_location() Returns the actor's location the client recieved during last tick. The method does not call the simulator.
        Hence, upon initializing, the world should first tick before calling get_location, or it could cause fatal bug"""
        # self.ego_vehicle.get_location()

        # add route planner for ego vehicle
        self.local_planner = LocalPlanner(self.ego_vehicle, {'sampling_resolution': self.sampling_resolution,
                                                             'buffer_size': self.buffer_size,
                                                             'vehicle_proximity': self.vehicle_proximity})
        # self.local_planner.set_global_plan(self.global_planner.get_route(
        #      self.map.get_waypoint(self.ego_vehicle.get_location())))
        self.current_lane = get_lane_center(self.map, self.ego_vehicle.get_location()).lane_id
        self.target_lane = get_lane_center(self.map, self.ego_vehicle.get_location()).lane_id
        self.new_target_lane = get_lane_center(self.map, self.ego_vehicle.get_location()).lane_id
        print('current lane, target lane', self.current_lane, self.target_lane)

        self.next_wps, o1, o2 = self.local_planner.run_step()
        self.state_waypoints, self.draw_waypoints = self.local_planner._get_waypoints_multilane()
        self.left_wps, self.center_wps, self.right_wps, self.left_rear_wps, self.center_rear_wps, self.right_rear_wps = self.state_waypoints
        self.vehicle_front, self.vehicle_rear, self.vehicle_inlane = self.local_planner._get_front_rear_inlane_vehicle()

        # set ego vehicle controller
        self._ego_autopilot(True)

        # Only use RL controller after ego vehicle speed reach speed_threshold
        self.speed_state = SpeedState.START
        self.controller = BasicAgent(self.ego_vehicle, {'target_speed': self.speed_threshold, 'dt': 1 / self.fps,
                                                        'max_throttle': self.throttle_bound,
                                                        'max_brake': self.brake_bound})
        # self.control_sigma={'Steer':random.choice([0.3, 0.4, 0.5]),
        #                 'Throttle_brake':random.choice([0.4,0.5,0.6])}
        self.control_sigma={'Steer': random.choice([0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]),
                            'Throttle_brake': random.choice([0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4])}
        # self.control_sigma={'Steer': random.choice([0,0]),
        #                     'Throttle_brake': random.choice([0,0])}

        self.autopilot_controller = Basic_Lanechanging_Agent(self.ego_vehicle, target_speed=30, opt_dict={'ignore_traffic_lights': True,
        'ignore_stop_signs': True, 'sampling_resolution': self.sampling_resolution, 'dt': 1.0/self.fps,
        'sampling_radius': self.sampling_resolution, 'max_steering': self.steer_bound, 'max_throttle': self.throttle_bound,
        'max_brake': self.brake_bound, 'buffer_size': self.buffer_size, 'ignore_front_vehicle': random.choice([True, False]),
        'ignore_change_gap': random.choice([True, False, False]), 'lanechanging_fps': random.choice([40, 50, 60])})


        # code for synchronous mode
        camera_bp = self.world.get_blueprint_library().find('sensor.camera.rgb')
        camera_transform = carla.Transform(carla.Location(x=1.5, z=2.4))
        self.camera = self.world.spawn_actor(camera_bp, camera_transform, attach_to=self.ego_vehicle)
        self.camera.listen(lambda image: self._sensor_callback(image, self.sensor_queue))

        # speed state switch
        if not self.debug:
            if not self.RL_switch and self.total_step < self.pre_train_steps:
                if not self.TM_switch:
                    if self.tm_control_episode == self.SWITCH_THRESHOLD:
                        self.TM_switch = True
                        self.tm_control_episode = 0
                        self.world.debug.draw_point(self.ego_spawn_point.location, size=0.2, life_time=300)
                    else:
                        self.tm_control_episode += 1
                else:
                    self.TM_switch = False
                    self.tm_control_episode += 1
                # if self.RL_switch:
                #     if self.rl_control_episode == self.SWITCH_THRESHOLD:
                #         self.RL_switch = False
                #         self.rl_control_episode = 0
                #         self.world.debug.draw_point(self.ego_spawn_point.location, size=0.2, life_time=0)
                #     else:
                #         self.rl_control_episode += 1
                #         # self.local_planner.set_global_plan(self.global_planner.get_route(
                #         #     self.map.get_waypoint(self.ego_vehicle.get_location())))
                # else:
                #     self.RL_switch = True
                #     self.rl_control_episode += 1
                #     # self.local_planner.set_global_plan(self.global_planner.get_route(
                #     #     self.map.get_waypoint(self.ego_vehicle.get_location())))
            else:
                self.RL_switch = True
                self.TM_switch = False
                # self.local_planner.set_global_plan(self.global_planner.get_route(
                #     self.map.get_waypoint(self.ego_vehicle.get_location())))
        else:
            # self.autopilot_controller.set_destination(random.choice(self.spawn_points).location)
            # self.autopilot_controller.set_destination(self.my_set_destination())
            pass

        # Update timesteps
        self.time_step = 0
        self.reset_step += 1

        # return state information
        return self._get_state({'state_waypoints': self.state_waypoints, 'vehicle_inlane': self.vehicle_inlane})

    def my_set_destination(self):
        # print('3')
        lane_id = get_lane_center(self.map, self.ego_vehicle.get_location())
        if lane_id == -1:
            lane_change = random.choice([1])
            if lane_change:
                return self.right_wps[-1].transform.location
        elif lane_id == -2:
            lane_change = random.choice([-1, 1])
            if lane_change == -1:
                return self.left_wps[-1].transform.location
            else:
                return self.right_wps[-1].transform.location
        elif lane_id == -3:
            lane_change = random.choice([1])
            if lane_change:
                return self.left_wps[-1].transform.location

    def step(self, action):
        print('speed_state: ', self.speed_state)
        self.autopilot_controller.set_info({'left_wps': self.left_wps, 'center_wps': self.center_wps,
                                            'right_wps': self.right_wps, 'left_rear_wps': self.left_rear_wps,
                                            'center_rear_wps': self.center_rear_wps, 'right_rear_wps': self.right_rear_wps,
                                            'vehicle_inlane': self.vehicle_inlane})
        # print("1.current lane, target lane, new_target_lane, last action, new action: ", self.current_lane,
        #       self.target_lane, self.new_target_lane, self.last_action, self.new_action)
        self.step_info = None
        self.next_wps = None
        self.vehicle_front = None
        """throttle (float):A scalar value to control the vehicle throttle [0.0, 1.0]. Default is 0.0.
                steer (float):A scalar value to control the vehicle steering [-1.0, 1.0]. Default is 0.0.
                brake (float):A scalar value to control the vehicle brake [0.0, 1.0]. Default is 0.0."""
        steer = np.clip(action[0][0], -self.steer_bound, self.steer_bound)
        # if action[0][1] >= 0:
        #     jump = action[0][1] * self.throttle_bound
        # else:
        #     jump = action[0][1] * self.brake_bound
        # if self.is_effective_action():
        #     self.throttle_brake += jump
        #     self.throttle_brake = np.clip(self.throttle_brake, -0.2, 0.8)
        # if self.throttle_brake < 0:
        #     brake = abs(self.throttle_brake)
        #     throttle = 0
        # else:
        #     brake = 0
        #     throttle = self.throttle_brake
        if action[0][1] >= 0:
            brake = 0
            throttle = np.clip(action[0][1], 0, self.throttle_bound)
        else:
            throttle = 0
            brake = np.clip(abs(action[0][1]), 0, self.brake_bound)

        # control = carla.VehicleControl(steer=float(steer), throttle=float(throttle), brake=float(brake),hand_brake=False,
        #                                reverse=False,manual_gear_shift=True,gear=1)
        control = carla.VehicleControl(steer=float(steer), throttle=float(throttle), brake=float(brake))

        # Only use RL controller after ego vehicle speed reach speed_threshold
        # Use DFA to calculate different speed state transition
        if not self.debug:
            control = self._speed_switch(control)
        else:
            # if self.autopilot_controller.done() and self.loop:
            #     # self.autopilot_controller.set_destination(random.choice(self.spawn_points).location)
            #     self.autopilot_controller.set_destination(self.my_set_destination())
            # control = self.autopilot_controller.run_step()
            print("1.current lane, target lane, new_target_lane, last action, new action: ", self.current_lane,
                  self.target_lane, self.new_target_lane, self.last_action, self.new_action)
            control, self.new_target_lane, self.new_action, self.distance_to_front_vehicles, self.distance_to_rear_vehicles = self.autopilot_controller.run_step(self.current_lane, self.target_lane, self.last_action)
            print("1.current lane, target lane, new_target_lane, last action, new action: ", self.current_lane, self.target_lane, self.new_target_lane, self.last_action, self.new_action)
            self.target_lane = self.new_target_lane
            self.last_action = self.new_action
        if self.sync:
            if not self.debug:
                if not self.RL_switch and not self.TM_switch:
                    # Add noise to autopilot controller's control command
                    print(f"Basic Agent Control Before Noise:{control}")
                    control.steer = np.clip(np.random.normal(control.steer,self.control_sigma['Steer']),-self.steer_bound,self.steer_bound)
                    if control.throttle > 0:
                        throttle_brake = control.throttle
                    else:
                        throttle_brake = -control.brake
                    throttle_brake = np.clip(np.random.normal(throttle_brake,self.control_sigma['Throttle_brake']),-self.brake_bound,self.throttle_bound)
                    if throttle_brake > 0:
                        control.throttle = throttle_brake
                        control.brake = 0
                    else:
                        control.throttle = 0
                        control.brake = abs(throttle_brake)
                if self.is_effective_action() and not self.TM_switch:
                    self.ego_vehicle.apply_control(control)
            else:
                control.steer = np.clip(np.random.normal(control.steer,self.control_sigma['Steer']),-self.steer_bound,self.steer_bound)
                if control.throttle > 0:
                    throttle_brake = control.throttle
                else:
                    throttle_brake = -control.brake
                throttle_brake = np.clip(np.random.normal(throttle_brake,self.control_sigma['Throttle_brake']),-self.brake_bound,self.throttle_bound)
                if throttle_brake > 0:
                    control.throttle = throttle_brake
                    control.brake = 0
                else:
                    control.throttle = 0
                    control.brake = abs(throttle_brake)
                self.ego_vehicle.apply_control(control)

            # print(self.map.get_waypoint(self.ego_vehicle.get_location(),False),self.ego_vehicle.get_transform(),sep='\n')
            # print(self.world.get_snapshot().timestamp)
            self.world.tick()
            """Attention: the server's tick function only returns after it ran a fixed_delta_seconds, so the client need not to wait for
            the server, the world snapshot of tick returned already include the next state after the uploaded action."""
            # print(self.map.get_waypoint(self.ego_vehicle.get_location(),False),self.ego_vehicle.get_transform(),sep='\n')
            # print(self.world.get_snapshot().timestamp)
            # print()
            # if self.is_effective_action():
            control = self.ego_vehicle.get_control()
            print("real control", control)
            last_lane = self.current_lane
            # print('4')
            self.current_lane = get_lane_center(self.map, self.ego_vehicle.get_location()).lane_id
            # print(self.ego_vehicle.get_speed_limit(),get_speed(self.ego_vehicle,False),get_acceleration(self.ego_vehicle,False),sep='\t')
            # route planner
            # self.next_wps, _, self.vehicle_front = self.local_planner.run_step()
            self.next_wps, o1, o2 = self.local_planner.run_step()
            self.state_waypoints, self.draw_waypoints = self.local_planner._get_waypoints_multilane()
            self.left_wps, self.center_wps, self.right_wps, self.left_rear_wps, self.center_rear_wps, self.right_rear_wps = self.state_waypoints
            self.vehicle_front, self.vehicle_rear, self.vehicle_inlane = self.local_planner._get_front_rear_inlane_vehicle()

            if self.debug:
                # run the ego vehicle with PID_controller
                if self.next_wps[0].id != self.former_wp.id:
                    self.former_wp = self.next_wps[0]
                # draw front waypoints
                # draw_waypoints(self.world, [self.next_wps[0]], 60, z=1)
                w1, w2, w3, w4, w5, w6 = self.draw_waypoints
                draw_waypoints(self.world, w1, 1.0/self.fps+0.001, z=1)
                draw_waypoints(self.world, w2, 1.0/self.fps+0.001, z=1)
                draw_waypoints(self.world, w3, 1.0/self.fps+0.001, z=1)
                # draw rear waypoints
                draw_waypoints(self.world, w4, 1.0/self.fps+0.001, z=1)
                draw_waypoints(self.world, w5, 1.0/self.fps+0.001, z=1)
                draw_waypoints(self.world, w6, 1.0/self.fps+0.001, z=1)
                # self.world.debug.draw_point(self.ego_vehicle.get_location(), size=0.1, life_time=5.0)
                control = None
                # control=self.controller.run_step({'waypoints':self.next_wps,'vehicle_front':self.vehicle_front})
                # print(control.steer,control.throttle,control.brake,sep='\t')
            else:
                w1, w2, w3, w4, w5, w6 = self.draw_waypoints
                draw_waypoints(self.world, w1, 1.0/self.fps+0.001, z=1)
                draw_waypoints(self.world, w2, 1.0/self.fps+0.001, z=1)
                draw_waypoints(self.world, w3, 1.0/self.fps+0.001, z=1)
                # draw rear waypoints
                draw_waypoints(self.world, w4, 1.0/self.fps+0.001, z=1)
                draw_waypoints(self.world, w5, 1.0/self.fps+0.001, z=1)
                draw_waypoints(self.world, w6, 1.0/self.fps+0.001, z=1)

            spectator = self.world.get_spectator()
            transform = self.ego_vehicle.get_transform()
            spectator.set_transform(carla.Transform(transform.location + carla.Location(z=80),
                                                    carla.Rotation(pitch=-90)))
            camera_data = self.sensor_queue.get(block=True)

            if self.ego_vehicle.get_location().distance(self.former_wp.transform.location) >= self.sampling_resolution:
                self.former_wp = self.next_wps[0]
            if self.vehicle_inlane[4] is not None:
                self.rear_vel_deque.append(get_speed(self.vehicle_inlane[4], False))
            else:
                self.rear_vel_deque.append(-1)
            """Attention: The sequence of following code is pivotal, do not recklessly chage their execution order"""
            reward = self._get_reward(self.last_action, last_lane, self.current_lane, self.distance_to_front_vehicles, self.distance_to_rear_vehicles)
            state = self._get_state({'state_waypoints': self.state_waypoints, 'vehicle_inlane': self.vehicle_inlane})
            self.step_info.update({'Reward': reward})
            self.last_acc = self.ego_vehicle.get_acceleration()
        else:
            temp = self.world.wait_for_tick()
            self.world.on_tick(lambda _: {})
            time.sleep(1.0 / self.fps)

        if self.debug:
            print(f"Speed:{get_speed(self.ego_vehicle, False)}, Acc:{get_acceleration(self.ego_vehicle, False)}")
        print(f"Current State:{self.speed_state}, RL In Control:{self.RL_switch}, TM In Control:{self.TM_switch}")
        if not self.RL_switch and not self.TM_switch:
            print(f"Control Sigma -- Steer:{self.control_sigma['Steer']}, Throttle_brake:{self.control_sigma['Throttle_brake']}")
        if self.is_effective_action():
            # update timesteps
            self.time_step += 1
            self.total_step += 1
            if self.speed_state == SpeedState.RUNNING and self.RL_switch == True:
                self.rl_control_step += 1
            # new_action \in [-1, 0, 1], but saved action is the index of max Q(s, a), and thus change \in [0, 1, 2]
            control_info = {'Steer': control.steer, 'Throttle': control.throttle, 'Brake': control.brake, 'Change': self.new_action+1}
            print(f"Ego Vehicle Speed Limit:{self.ego_vehicle.get_speed_limit() * 3.6}\n"
                  f"Episode:{self.reset_step}, Total_step:{self.total_step}, Time_step:{self.time_step}, RL_control_step:{self.rl_control_step}, \n"
                  f"Vel: {get_speed(self.ego_vehicle, False)}, Acc:{get_acceleration(self.ego_vehicle, False)}, distance:{state['vehicle_info'][0][0] * self.vehicle_proximity}, \n"
                  f"Reward:{self.step_info['Reward']}, TTC:{self.step_info['TTC']}, Comfort:{self.step_info['Comfort']}, "
                  f"Efficiency:{self.step_info['Efficiency']}, Lane_center:{self.step_info['Lane_center']}, Yaw:{self.step_info['Yaw']} \n"
                  f"Steer:{control_info['Steer']}, Throttle:{control_info['Throttle']}, Brake:{control_info['Brake']}")
            # print(f"Steer:{control_info['Steer']}, Throttle:{control_info['Throttle']}, Brake:{control_info['Brake']}\n")

            return state, reward, self._truncated(), self._done(), self._get_info(control_info)
        else:
            return state, reward, self._truncated(), self._done(), self._get_info()

    def get_observation_space(self):
        """
        TODO
        :return:
        """
        """Get observation space of cureent environment"""
        return {'waypoints': 10, 'ego_vehicle': 6, 'conventional_vehicle': 3}

    def get_action_bound(self):
        """Return action bound of ego vehicle controller"""
        return {'steer': self.steer_bound, 'throttle': self.throttle_bound, 'brake': self.brake_bound}

    def is_effective_action(self):
        # testing if current ego vehcle's action should be put into replay buffer
        return self.speed_state == SpeedState.REBOOT or self.speed_state == SpeedState.RUNNING

    def seed(self, seed=None):
        return

    def render(self, mode):
        pass

    def process_lane_wp(self, wps_list, ego_vehicle_z, ego_forward_vector, my_sample_ratio, flag, t):
        wps = []
        idx = 0
        final_t = t
        if flag == -1:
            final_t = 1.5 + t
        elif flag == 1:
            final_t = 1.5 - t

        for wp in wps_list:
            delta_z = wp.transform.location.z - ego_vehicle_z
            yaw_diff = math.degrees(get_yaw_diff(wp.transform.get_forward_vector(), ego_forward_vector))
            yaw_diff = yaw_diff / 90
            if idx % my_sample_ratio == my_sample_ratio-1:
                wps.append([delta_z/3, yaw_diff, final_t])
            idx = idx + 1
        return np.array(wps)

    def process_veh(self, vehicle_inlane, left_wall, right_wall):
        ego_speed = get_speed(self.ego_vehicle, False)
        ego_location = self.ego_vehicle.get_location()
        # print('5')
        lane_center = get_lane_center(self.map, ego_location)
        ego_bounding_x = self.ego_vehicle.bounding_box.extent.x
        ego_bounding_y = self.ego_vehicle.bounding_box.extent.y
        max_speed = 10
        all_v_info = []
        print('vehicle_inlane: ', vehicle_inlane)
        for i in range(6):
            veh = vehicle_inlane[i]
            wall = False
            if left_wall and (i == 0 or i == 3):
                wall = True
            if right_wall and (i == 2 or i == 5):
                wall = True
            if wall:
                v_info = [0, 0, 0]
            else:
                if veh is None:
                    v_info = [1, 0, 0]
                else:
                    veh_speed = get_speed(veh, False)
                    rel_speed = ego_speed - veh_speed

                    distance = ego_location.distance(veh.get_location())
                    vehicle_len = max(abs(ego_bounding_x), abs(ego_bounding_y)) + \
                        max(abs(veh.bounding_box.extent.x), abs(veh.bounding_box.extent.y))
                    distance -= vehicle_len

                    right_lane_dis = lane_center.get_right_lane().transform.location.distance(
                        self.ego_vehicle.get_location())
                    t = lane_center.lane_width / 2 + lane_center.get_right_lane().lane_width / 2 - right_lane_dis
                    if distance < self.min_distance:
                        v_info = [0, rel_speed / max_speed, t]
                    else:
                        distance -= self.min_distance
                        v_info = [distance / (self.vehicle_proximity - self.min_distance), rel_speed / max_speed, t]
            all_v_info.append(v_info)
        # print(all_v_info)
        return np.array(all_v_info)

    def _get_state(self, dict):
        """return a tuple: the first element is next waypoints, the second element is vehicle_front information"""

        # The wps_length here is a litle tricky, compared with the commented version
        # wps_length=dict['waypoints'][-1].transform.location.distance(self.ego_vehicle.get_location())
        wps_length = self.sampling_resolution * self.buffer_size
        wps = []
        # {'state_waypoints': self.state_waypoints, 'vehicle_inlane': self.vehicle_inlane}
        left_wps, center_wps, right_wps, left_rear_wps, center_rear_wps, right_rear_wps = dict['state_waypoints']
        left_front_veh, center_front_veh, right_front_veh, left_rear_veh, center_rear_veh, right_rear_veh = dict['vehicle_inlane']

        # print(self.ego_vehicle.get_transform().rotation,
        #     dict['waypoints'][0].road_id,dict['waypoints'][0].lane_id,dict['waypoints'][0].transform.rotation,
        #     dict['waypoints'][1].road_id,dict['waypoints'][1].lane_id,dict['waypoints'][1].transform.rotation,
        #     dict['waypoints'][2].road_id,dict['waypoints'][2].lane_id,dict['waypoints'][2].transform.rotation,sep='\t')
        # if dict['waypoints']:
        #     for wp in dict['waypoints']:
        #         lane_center = get_lane_center(self.map,self.ego_vehicle.get_location())
        #         #distance = self.ego_vehicle.get_location().distance(wp.transform.location)
        #         distance = lane_center.transform.location.distance(wp.transform.location)
        #         yaw_diff = math.degrees(get_yaw_diff(wp.transform.get_forward_vector(),
        #                                              self.ego_vehicle.get_transform().get_forward_vector()))
        #         yaw_diff /= 90
        #
        #         wps.append([distance / wps_length, yaw_diff])
        # if len(wps) < self.buffer_size:
        #     # end of route, not enough next waypoints
        #     gap = self.buffer_size - len(wps)
        #     for _ in range(gap):
        #         wps.append([(len(wps) + 1) * self.sampling_resolution / wps_length, 0])
        lane_center = get_lane_center(self.map, self.ego_vehicle.get_location())
        right_lane_dis = lane_center.get_right_lane().transform.location.distance(self.ego_vehicle.get_location())
        t = lane_center.lane_width / 2 + lane_center.get_right_lane().lane_width / 2 - right_lane_dis

        ego_vehicle_z = get_lane_center(self.map, self.ego_vehicle.get_location()).transform.location.z
        ego_forward_vector = self.ego_vehicle.get_transform().get_forward_vector()
        my_sample_ratio = self.buffer_size // 10
        center_wps_processed = self.process_lane_wp(center_wps, ego_vehicle_z, ego_forward_vector, my_sample_ratio, 0, t)
        if len(left_wps) == 0:
            left_wps_processed = center_wps_processed.copy()
        else:
            left_wps_processed = self.process_lane_wp(left_wps, ego_vehicle_z, ego_forward_vector, my_sample_ratio, -1, t)
        if len(right_wps) == 0:
            right_wps_processed = center_wps_processed.copy()
        else:
            right_wps_processed = self.process_lane_wp(right_wps, ego_vehicle_z, ego_forward_vector, my_sample_ratio, 1, t)
        left_wall = False
        if len(left_wps) == 0:
            left_wall = True
        right_wall = False
        if len(right_wps) == 0:
            right_wall = True
        vehicle_inlane_processed = self.process_veh(dict['vehicle_inlane'], left_wall, right_wall)
        # if dict['vehicle_front']:
        #     vehicle_front = dict['vehicle_front']
        #     ego_speed = get_speed(self.ego_vehicle, False)
        #     vf_speed = get_speed(vehicle_front, False)
        #     rel_speed = ego_speed - vf_speed
        #     distance = self.ego_vehicle.get_location().distance(vehicle_front.get_location())
        #     vehicle_len = max(abs(self.ego_vehicle.bounding_box.extent.x), abs(self.ego_vehicle.bounding_box.extent.y))+ \
        #         max(abs(self.vehicle_front.bounding_box.extent.x), abs(self.vehicle_front.bounding_box.extent.y))
        #     distance -= vehicle_len
        #     if distance < self.min_distance:
        #         vfl=[0, rel_speed/5]
        #     else:
        #         distance -= self.min_distance
        #         vfl = [distance / (self.vehicle_proximity-self.min_distance), rel_speed/5]
        # else:
        #     # No vehicle front, suppose there is a vehicle at the end of waypoint list and relative speed is 0
        #     vfl = [1, 0]

        # ego vehicle information
        # print('6')

        yaw_diff_ego = math.degrees(get_yaw_diff(lane_center.transform.get_forward_vector(),
                                               self.ego_vehicle.get_transform().get_forward_vector()))

        yaw_forward = lane_center.transform.get_forward_vector()
        v_3d = self.ego_vehicle.get_velocity()
        theta_v = get_yaw_diff(v_3d, yaw_forward)
        v_s = v_3d.length() * math.cos(theta_v)
        v_t = v_3d.length() * math.sin(theta_v)
        # v_t1=v_3d.length()*math.cos(alpha_v)

        a_3d = self.ego_vehicle.get_acceleration()
        theta_a = get_yaw_diff(a_3d, yaw_forward)
        a_s = a_3d.length() * math.cos(theta_a)
        a_t = a_3d.length() * math.sin(theta_a)

        """Attention:
        Upon initializing, there are some bugs in the theta_v and theta_a, which could be greater than 90,
        this might be caused by carla."""
        return {'left_waypoints': left_wps_processed, 'center_waypoints': center_wps_processed,
                'right_waypoints': right_wps_processed, 'ego_vehicle': [v_s/10, v_t/10, a_s/3, a_t/3, t, yaw_diff_ego/90],
                'vehicle_info': vehicle_inlane_processed}

    def calculate_lane_change_reward(self, last_action, last_lane, current_lane, distance_to_front_vehicles, distance_to_rear_vehicles):
        print('distance_to_front_vehicles, distance_to_rear_vehicles: ', distance_to_front_vehicles, distance_to_rear_vehicles)
        # still the distances of the last time step
        reward = 0
        center_front_dis = distance_to_front_vehicles[1]
        if current_lane - last_lane == -1:
            # change right
            self.calculate_impact = True
            right_front_dis = distance_to_front_vehicles[2]
            if right_front_dis > center_front_dis:
                reward = min((right_front_dis / center_front_dis - 1) * self.lane_change_reward, self.lane_change_reward)
            else:
                reward = max((right_front_dis / center_front_dis - 1) * self.lane_change_reward, -self.lane_change_reward)
            print('reward1: ', reward)
            reward = reward + self.calculate_rear_ttc_reward()
            print('reward2: ', reward)
        elif current_lane - last_lane == 1:
            # change left
            self.calculate_impact = True
            left_front_dis = distance_to_front_vehicles[0]
            if left_front_dis > center_front_dis:
                reward = min((left_front_dis / center_front_dis - 1) * self.lane_change_reward, self.lane_change_reward)
            else:
                reward = max((left_front_dis / center_front_dis - 1) * self.lane_change_reward, -self.lane_change_reward)
            print('reward1: ', reward)
            reward = reward + self.calculate_rear_ttc_reward()
            print('reward2: ', reward)

        return reward

    def _get_reward(self, last_action, last_lane, current_lane, distance_to_front_vehicles, distance_to_rear_vehicles):
        """Calculate the step reward:
        TTC: Time to collide with front vehicle
        Eff: Ego vehicle efficiency, speed ralated
        Com: Ego vehicle comfort, ego vehicle acceration change rate
        Lcen: Distance between ego vehicle location and lane center
        """
        ego_speed = get_speed(self.ego_vehicle, True)
        # print('7')
        lane_center = get_lane_center(self.map, self.ego_vehicle.get_location())
        TTC = float('inf')
        if self.vehicle_front:
            distance = self.ego_vehicle.get_location().distance(self.vehicle_front.get_location())
            vehicle_len = max(abs(self.ego_vehicle.bounding_box.extent.x),abs(self.ego_vehicle.bounding_box.extent.y)) + \
                max(abs(self.vehicle_front.bounding_box.extent.x),abs(self.vehicle_front.bounding_box.extent.y))
            distance -= vehicle_len
            if distance < self.min_distance:
                TTC = 0.01
            else:
                distance -= self.min_distance
                rel_speed = ego_speed / 3.6 - get_speed(self.vehicle_front, False)
                if abs(rel_speed) > float(0.0000001):
                    TTC = distance / rel_speed
            #print(distance, TTC)
        # fTTC=-math.exp(-TTC)
        if TTC >= 0 and TTC <= self.TTC_THRESHOLD:
            fTTC = np.clip(np.log(TTC / self.TTC_THRESHOLD), -1, 0)
        else:
            fTTC = 0

        yaw_forward = lane_center.transform.get_forward_vector().make_unit_vector()
        v_3d = self.ego_vehicle.get_velocity()
        # ignore z value
        v_3d.z = 0
        if v_3d.length() != 0.0:
            theta_v = math.acos(np.clip(v_3d.dot(yaw_forward) / (v_3d.length() * yaw_forward.length()), -1, 1))
            # alpha_v = math.acos(np.clip(v_3d.dot(yaw_right)/(v_3d.length()*yaw_right.length()),-1,1))
        else:
            theta_v = math.acos(0)
            # alpha_v=math.acos(0)
        v_s = v_3d.length() * math.cos(theta_v)
        if v_s*3.6 > self.speed_limit:
            # fEff = 1
            fEff = math.exp(self.speed_limit - v_s * 3.6) - 1
        else:
            fEff = v_s * 3.6 / self.speed_limit - 1

        cur_acc = self.ego_vehicle.get_acceleration()
        # jerk = (cur_acc.x - self.last_acc.x) ** 2 / (1.0 / self.fps) + (cur_acc.y - self.last_acc.y) ** 2 / (
        #         1.0 / self.fps)
        jerk = ((cur_acc.x - self.last_acc.x) * self.fps) ** 2 + ((cur_acc.y - self.last_acc.y) * self.fps) ** 2
        # whick still requires further testing, longitudinal and lateral
        fCom = -jerk / ((6 * self.fps) ** 2 + (12 * self.fps) ** 2)
        if self.guide_change:
            Lcen, fLcen = self.calculate_guide_lane_center(lane_center, self.ego_vehicle.get_location(), distance_to_front_vehicles, distance_to_rear_vehicles)
        else:
            Lcen = lane_center.transform.location.distance(self.ego_vehicle.get_location())
            # print(
            #     f"Lane Center:{Lcen}, Road ID:{lane_center.road_id}, Lane ID:{lane_center.lane_id}, Yaw:{self.ego_vehicle.get_transform().rotation.yaw}")
            if not test_waypoint(lane_center, True) or Lcen > lane_center.lane_width / 2 + 0.1:
                fLcen = -1.5
                print('lane_center.lane_id, lcen, flcen: ', lane_center.lane_id, lane_center.road_id, Lcen, fLcen, lane_center.lane_width / 2)
            else:
                fLcen = - Lcen / (lane_center.lane_width / 2)

        yaw_diff = math.degrees(get_yaw_diff(lane_center.transform.get_forward_vector(),
                                self.ego_vehicle.get_transform().get_forward_vector()))
        fYaw = -abs(yaw_diff) / 90

        impact = 0
        if self.calculate_impact:
            last_rear_vel = self.rear_vel_deque[0]
            current_rear_vel = self.rear_vel_deque[1]
            if last_rear_vel == -1 or current_rear_vel == -1:
                impact = 0
            else:
                if current_rear_vel < last_rear_vel:
                    impact = (current_rear_vel - last_rear_vel) / (9 * 0.1)
            self.calculate_impact = False

        # reward for lane_changing
        lane_changing_reward = self.calculate_lane_change_reward(last_action, last_lane, current_lane, distance_to_front_vehicles, distance_to_rear_vehicles)

        self.step_info = {'velocity': v_s, 'offlane': Lcen, 'yaw_diff': yaw_diff, 'TTC': fTTC, 'Comfort': fCom,
                          'Efficiency': fEff, 'Lane_center': fLcen, 'Yaw': fYaw,
                          'lane_changing_reward': lane_changing_reward, 'impact': impact, 'Abandon': False}
        print('reward_info: ', self.step_info)
        if self._truncated():
            history, tags = self.collision_sensor.get_collision_history()
            if len(history) != 0:
                if SemanticTags.Vehicles in tags:
                    return - self.penalty
                else:
                    # If ego vehicle collides with traffic lights and stop signs, do not add penalty
                    self.step_info['Abandon'] = True
                    return fTTC + fEff * 2 + fCom + fLcen + impact + lane_changing_reward
            else:
                return - self.penalty
        else:
            return fTTC + fEff * 2 + fCom + fLcen + impact + lane_changing_reward

    def calculate_guide_lane_center(self, lane_center, location, front_distance, rear_distance):
        left = False
        right = False
        if lane_center.lane_id != -1 and front_distance[0] > 25 and front_distance[0]/front_distance[1] > 1.2 and rear_distance[0] > 25:
            left = True
        if lane_center.lane_id != -3 and front_distance[2] > 25 and front_distance[2]/front_distance[1] > 1.2 and rear_distance[2] > 25:
            right = True
        if left:
            Lcen = lane_center.get_left_lane().transform.location.distance(location)
            fLcen = - Lcen / lane_center.lane_width
        elif right:
            Lcen = lane_center.get_right_lane().transform.location.distance(location)
            fLcen = - Lcen / lane_center.lane_width
        else:
            Lcen = lane_center.transform.location.distance(self.ego_vehicle.get_location())
            # print(
            #     f"Lane Center:{Lcen}, Road ID:{lane_center.road_id}, Lane ID:{lane_center.lane_id}, Yaw:{self.ego_vehicle.get_transform().rotation.yaw}")
            if not test_waypoint(lane_center, True) or Lcen > lane_center.lane_width / 2 + 0.1:
                fLcen = -1.5
                print('lane_center.lane_id, lcen, flcen: ', lane_center.lane_id, lane_center.road_id, Lcen, fLcen, lane_center.lane_width / 2)
            else:
                fLcen = - Lcen / (lane_center.lane_width / 2)
        return Lcen, fLcen

    def calculate_guide_lane_center_pdqn(self, lane_center, location, front_distance, rear_distance):
        pass

    def calculate_rear_ttc_reward(self):
        ego_speed = get_speed(self.ego_vehicle, True)
        TTC = float('inf')
        if self.vehicle_rear:
            distance = self.ego_vehicle.get_location().distance(self.vehicle_rear.get_location())
            vehicle_len = max(abs(self.ego_vehicle.bounding_box.extent.x),
                              abs(self.ego_vehicle.bounding_box.extent.y)) + \
                          max(abs(self.vehicle_rear.bounding_box.extent.x),
                              abs(self.vehicle_rear.bounding_box.extent.y))
            distance -= vehicle_len
            if distance < self.min_distance:
                TTC = 0.01
            else:
                distance -= self.min_distance
                rel_speed = get_speed(self.vehicle_rear, False) - ego_speed / 3.6
                if abs(rel_speed) > float(0.0000001):
                    TTC = distance / rel_speed
            # print(distance, TTC)
        # fTTC=-math.exp(-TTC)
        if TTC >= 0 and TTC <= self.TTC_THRESHOLD:
            fTTC = np.clip(np.log(TTC / self.TTC_THRESHOLD), -1, 0)
        else:
            fTTC = 0

        return fTTC

    def _speed_switch(self, cont):
        """cont: the control command of RL agent"""
        ego_speed = get_speed(self.ego_vehicle)
        control = cont
        if self.speed_state == SpeedState.START:
            # control = self.controller.run_step({'waypoints':self.next_wps,'vehicle_front':self.vehicle_front})
            if ego_speed >= self.speed_threshold:
                self.speed_state = SpeedState.RUNNING
                if not self.RL_switch:
                    if self.TM_switch:
                        # Under traffic manager control
                        self._ego_autopilot(True)
                    else:
                        # Under basic agent control
                        self._ego_autopilot(False)
                        # self.autopilot_controller.set_destination(random.choice(self.spawn_points).location)
                        # if self.autopilot_controller.done() and self.loop:
                        #     self.autopilot_controller.set_destination(self.my_set_destination())
                        # control = self.autopilot_controller.run_step()
                        print("2.current lane, target lane, new_target_lane, last action, new action: ",
                              self.current_lane, self.target_lane, self.new_target_lane, self.last_action,
                              self.new_action)
                        control, self.new_target_lane, self.new_action, self.distance_to_front_vehicles, self.distance_to_rear_vehicles = self.autopilot_controller.run_step(self.current_lane, self.target_lane, self.last_action)
                        print("2.current lane, target lane, new_target_lane, last action, new action: ",
                              self.current_lane, self.target_lane, self.new_target_lane, self.last_action,
                              self.new_action)
                        self.target_lane = self.new_target_lane
                        self.last_action = self.new_action
                else:
                    self._ego_autopilot(False)
            else:
                print("21.current lane, target lane, new_target_lane, last action, new action: ",
                      self.current_lane, self.target_lane, self.new_target_lane, self.last_action,
                      self.new_action)
        elif self.speed_state == SpeedState.RUNNING:
            if self.RL_switch == True:
                if ego_speed < self.speed_min:
                    # Only add reboot state in the beginning 200 episodes
                    # self._ego_autopilot(True)
                    #self.speed_state = SpeedState.REBOOT
                    pass
                pass
            else:
                if self.TM_switch:
                    #Under traffic manager control
                    pass
                else:
                    #Under basic agent control
                    # if self.autopilot_controller.done() and self.loop:
                    #     # self.autopilot_controller.set_destination(random.choice(self.spawn_points).location)
                    #     self.autopilot_controller.set_destination(self.my_set_destination())
                    # control=self.autopilot_controller.run_step()
                    print("3.current lane, target lane, new_target_lane, last action, new action: ", self.current_lane,
                          self.target_lane, self.new_target_lane, self.last_action, self.new_action)
                    control, self.new_target_lane, self.new_action, self.distance_to_front_vehicles, self.distance_to_rear_vehicles = self.autopilot_controller.run_step(self.current_lane, self.target_lane, self.last_action)
                    print("3.current lane, target lane, new_target_lane, last action, new action: ", self.current_lane,
                          self.target_lane, self.new_target_lane, self.last_action, self.new_action)
                    self.target_lane = self.new_target_lane
                    self.last_action = self.new_action
        elif self.speed_state == SpeedState.REBOOT:
            control = self.controller.run_step({'waypoints': self.next_wps, 'vehicle_front': self.vehicle_front})
            if ego_speed >= self.speed_threshold:
                # self._ego_autopilot(False)
                self.speed_state = SpeedState.RUNNING
        else:
            logging.error('CODE LOGIC ERROR')

        return control

    def _truncated(self):
        """Calculate whether to terminate the current episode"""
        if len(self.collision_sensor.get_collision_history()[0]) != 0:
            # Here we judge speed state because there might be collision event when spawning vehicles
            logging.warn('collison happend')
            return True
        if self.map.get_waypoint(self.ego_vehicle.get_location()) is None:
            logging.warn('vehicle drive out of road')
            return True
        if get_speed(self.ego_vehicle, False) < 0.1 and self.speed_state != SpeedState.START:
            logging.warn('vehicle speed too low')
            return True
        # if self.lane_invasion_sensor.get_invasion_count()!=0:
        #     logging.warn('lane invasion occur')
        #     return True
        if self.step_info['Lane_center'] == -1.5:
            logging.warn('lane invasion occur')
            return True

        return False

    def _done(self):
        if self.RL_switch and self.next_wps[2].transform.location.distance(
                self.ego_spawn_point.location) < self.sampling_resolution:
            # The local planner's waypoint list has been depleted
            logging.info('vehicle reach destination, simulation terminate')
            return True
        if not self.RL_switch:
            if self.time_step > 5000:
                # Let the traffic manager only execute 5000 steps. or it can fill the replay buffer
                logging.info('5000 steps passed under traffic manager control')
                return True
            if self.next_wps[2].transform.location.distance(
                    self.ego_spawn_point.location) < self.sampling_resolution:
                # The second next waypoints is close enough to the spawn point, route done
                logging.info('vehicle reach destination under basic agent, simulation terminate')
                return True

        return False

    def _get_info(self, control_info=None):
        """Rerurn simulation running information,
            param: control_info, the current controller information
        """
        if control_info is None:
            return self.step_info
        else:
            self.step_info.update(control_info)
            return self.step_info

    def _ego_autopilot(self, setting=True):
        # Use traffic manager to control ego vehicle
        self.ego_vehicle.set_autopilot(setting, self.tm_port)
        if setting:
            speed_diff = (30 * 3.6 - self.speed_limit) / (30 * 3.6) * 100
            self.traffic_manager.distance_to_leading_vehicle(self.ego_vehicle, self.min_distance)
            self.traffic_manager.ignore_lights_percentage(self.ego_vehicle, 100)
            self.traffic_manager.ignore_signs_percentage(self.ego_vehicle, 100)
            self.traffic_manager.ignore_vehicles_percentage(self.ego_vehicle, 0)
            self.traffic_manager.ignore_walkers_percentage(self.ego_vehicle, 100)
            self.traffic_manager.vehicle_percentage_speed_difference(self.ego_vehicle, speed_diff)
            if self.auto_lanechange and self.speed_state == SpeedState.RUNNING:
                self.traffic_manager.auto_lane_change(self.ego_vehicle, True)
                self.traffic_manager.random_left_lanechange_percentage(self.ego_vehicle, 100)
                self.traffic_manager.random_right_lanechange_percentage(self.ego_vehicle, 100)


            # self.traffic_manager.set_desired_speed(self.ego_vehicle, 72)
            # ego_wp=self.map.get_waypoint(self.ego_vehicle.get_location())
            # self.traffic_manager.set_path(self.ego_vehicle,path)
            """set_route(self, actor, path):
                Sets a list of route instructions for a vehicle to follow while controlled by the Traffic Manager. 
                The possible route instructions are 'Left', 'Right', 'Straight'.
                The traffic manager only need this instruction when faces with a junction."""
            self.traffic_manager.set_route(self.ego_vehicle,
                                           ['Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight'])

    def _sensor_callback(self, sensor_data, sensor_queue):
        array = np.frombuffer(sensor_data.raw_data, dtype=np.dtype('uint8'))
        # image is rgba format
        array = np.reshape(array, (sensor_data.height, sensor_data.width, 4))
        array = array[:, :, :3]
        sensor_queue.put((sensor_data.frame, array))

    def _create_vehicle_blueprint(self, actor_filter, ego=False, color=None, number_of_wheels=[4]):
        """Create the blueprint for a specific actor type.

        Args:
            actor_filter: a string indicating the actor type, e.g, 'vehicle.lincoln*'.

        Returns:
            bp: the blueprint object of carla.
        """
        blueprints = list(self.world.get_blueprint_library().filter(actor_filter))
        if not ego:
            for bp in blueprints:
                if bp.has_attribute(self.ego_filter):
                    blueprints.remove(bp)

        blueprint_library = []
        for nw in number_of_wheels:
            blueprint_library = blueprint_library + [x for x in blueprints if
                                                     int(x.get_attribute('number_of_wheels')) == nw]
        bp = random.choice(blueprint_library)
        if bp.has_attribute('color'):
            if color is None:
                color = random.choice(bp.get_attribute('color').recommended_values)
            bp.set_attribute('color', color)
        if bp.has_attribute('driver_id'):
            driver_id = random.choice(bp.get_attribute('driver_id').recommended_values)
            bp.set_attribute('driver_id', driver_id)
        if not ego:
            bp.set_attribute('role_name', 'autopilot')
        else:
            bp.set_attribute('role_name', 'hero')

        # bp.set_attribute('sticky_control', False)
        return bp

    def _init_renderer(self):
        """Initialize the birdeye view renderer."""
        pass

    def _set_synchronous_mode(self):
        """Set whether to use the synchronous mode."""
        # Set fixed simulation step for synchronous mode
        if self.sync:
            settings = self.world.get_settings()
            settings.no_rendering_mode = self.no_rendering
            if not settings.synchronous_mode:
                settings.synchronous_mode = True
                settings.fixed_delta_seconds = 1.0 / self.fps
                self.world.apply_settings(settings)

    def _set_traffic_manager(self):
        self.traffic_manager = self.client.get_trafficmanager(self.tm_port)
        # every vehicle keeps a distance of 3.0 meter
        self.traffic_manager.set_global_distance_to_leading_vehicle(10)
        # Set physical mode only for cars around ego vehicle to save computation
        if self.hybrid:
            self.traffic_manager.set_hybrid_physics_mode(True)
            self.traffic_manager.set_hybrid_physics_radius(70.0)

        """The default global speed limit is 30 m/s
        Vehicles' target speed is 70% of their current speed limit unless any other value is set."""
        speed_diff = (30 * 3.6 - (self.speed_limit+1)) / (30 * 3.6) * 100
        # Let the companion vehicles drive a bit faster than ego speed limit
        self.traffic_manager.global_percentage_speed_difference(0)
        self.traffic_manager.set_synchronous_mode(self.sync)

    def _try_spawn_ego_vehicle_at(self, transform):
        """Try to spawn a  vehicle at specific transform
        Args:
            transform: the carla transform object.

        Returns:
            Bool indicating whether the spawn is successful.
        """
        vehicle = None
        # Check if ego position overlaps with surrounding vehicles
        overlap = False
        for idx, poly in self.vehicle_polygons[-1].items():
            poly_center = np.mean(poly, axis=0)
            ego_center = np.array([transform.location.x, transform.location.y])
            dis = np.linalg.norm(poly_center - ego_center)
            if dis > 8:
                continue
            else:
                overlap = True
                break

        if not overlap:
            ego_bp = self._create_vehicle_blueprint(self.ego_filter, ego=True, color='0,255,0')
            vehicle = self.world.try_spawn_actor(ego_bp, transform)
            if vehicle is None:
                logging.warn("Ego vehicle generation fail")

        # if self.debug and vehicle:
        #      vehicle.show_debug_telemetry()

        return vehicle

    def _spawn_companion_vehicles(self):
        """
        Spawn surrounding vehcles of this simulation
        each vehicle is set to autopilot mode and controled by Traffic Maneger
        note: the ego vehicle trafficmanager and companion vehicle trafficmanager shouldn't be the same one
        """
        # spawn_points_ = self.map.get_spawn_points()
        spawn_points_ = self.spawn_points
        # make sure companion vehicles also spawn on chosen route
        # spawn_points_=[x.transform for x in self.ego_spawn_waypoints]

        num_of_spawn_points = len(spawn_points_)
        num_of_vehicles=random.choice(self.num_of_vehicles)

        if num_of_vehicles < num_of_spawn_points:
            random.shuffle(spawn_points_)
            spawn_points = random.sample(spawn_points_, num_of_vehicles)
        else:
            msg = 'requested %d vehicles, but could only find %d spawn points'
            logging.warning(msg, num_of_vehicles, num_of_spawn_points)
            num_of_vehicles = num_of_spawn_points - 1

        # Use command to apply actions on batch of data
        SpawnActor = carla.command.SpawnActor
        SetAutopilot = carla.command.SetAutopilot
        FutureActor = carla.command.FutureActor  # FutureActor is eaqual to 0
        command_batch = []

        for i, transform in enumerate(spawn_points_):
            if i >= num_of_vehicles:
                break

            # print(transform)
            blueprint = self._create_vehicle_blueprint('vehicle.audi.etron', number_of_wheels=[4])
            # Spawn the cars and their autopilot all together
            # Spawn the cars and their autopilot all together
            command_batch.append(SpawnActor(blueprint, transform).
                                 then(SetAutopilot(FutureActor, True, self.tm_port)))

        # execute the command batch
        for (i, response) in enumerate(self.client.apply_batch_sync(command_batch, self.sync)):
            if response.has_error():
                logging.error(response.error)
            else:
                # print("Future Actor",response.actor_id)
                self.companion_vehicles.append(self.world.get_actor(response.actor_id))
                self.traffic_manager.ignore_lights_percentage(
                    self.world.get_actor(response.actor_id), 100)
                self.traffic_manager.auto_lane_change(
                    self.world.get_actor(response.actor_id), False)
                self.traffic_manager.ignore_signs_percentage(
                    self.world.get_actor(response.actor_id), 100)
                self.traffic_manager.ignore_walkers_percentage(
                    self.world.get_actor(response.actor_id), 100)
                self.traffic_manager.set_route(self.world.get_actor(response.actor_id),
                                               ['Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight', 'Straight'])
                self.traffic_manager.update_vehicle_lights(
                    self.world.get_actor(response.actor_id), True)
                # print(self.world.get_actor(response.actor_id).attributes)

        msg = 'requested %d vehicles, generate %d vehicles, press Ctrl+C to exit.'
        logging.info(msg, num_of_vehicles, len(self.companion_vehicles))

    def _try_spawn_random_walker_at(self, transform):
        """Try to spawn a walker at specific transform with random bluprint.

        Args:
            transform: the carla transform object.

        Returns:
            Bool indicating whether the spawn is successful.
        """
        pass

    def _clear_actors(self, actor_filters, filter=True):
        """Clear specific actors
        filter: True means filter actors by blueprint, Fals means fiter actors by carla.CityObjectLabel"""
        if filter:
            for actor_filter in actor_filters:
                self.client.apply_batch([carla.command.DestroyActor(x)
                                         for x in self.world.get_actors().filter(actor_filter)])

        # for actor_filter in actor_filters:
        #     for actor in self.world.get_actors().filter(actor_filter):
        #         if actor.is_alive:
        #             if actor.type_id =='controller.ai.walker':
        #                 actor.stop()
        #             actor.destroy()
