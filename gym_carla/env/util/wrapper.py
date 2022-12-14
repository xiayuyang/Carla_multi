import carla
import math
import numpy as np
from enum import Enum
from gym_carla.env.util.misc import get_speed,get_yaw_diff,test_waypoint,get_sign

class WaypointWrapper:
    """The location left, right, center is allocated according to the lane of ego vehicle"""
    left_front_wps=None
    left_rear_wps=None
    center_front_wps=None
    center_rear_wps=None
    right_front_wps=None
    right_rear_wps=None
    
    def __init__(self,opt=None) -> None:
        if not opt:
            WaypointWrapper.left_front_wps=None
            WaypointWrapper.left_rear_wps=None
            WaypointWrapper.center_front_wps=None
            WaypointWrapper.center_rear_wps=None
            WaypointWrapper.right_front_wps=None
            WaypointWrapper.right_rear_wps=None
        else:
            if 'left_front_wps' in opt:
                WaypointWrapper.left_front_wps=opt['left_front_wps']
            if 'left_rear_wps' in opt:
                WaypointWrapper.left_rear_wps=opt['left_rear_wps']
            if 'center_front_wps' in opt:
                WaypointWrapper.center_front_wps=opt['center_front_wps']
            if 'center_rear_wps' in opt:
                WaypointWrapper.center_rear_wps=opt['center_rear_wps']
            if 'right_front_wps' in opt:
                WaypointWrapper.right_front_wps=opt['right_front_wps']
            if 'right_rear_wps' in opt:
                WaypointWrapper.right_rear_wps=opt['right_rear_wps']


class VehicleWrapper:
    """The location left, right, center is allocated according to the lane of ego vehicle"""
    left_front_veh=None
    left_rear_veh=None
    center_front_veh=None
    center_rear_veh=None
    right_front_veh=None
    right_rear_veh=None
    """distance sequence:
    distance_to_front_vehicles:[left_front_veh,center_front_veh,right_front_veh]
    distance_to_rear_vehicles:[left_rear_veh,center_rear_veh,right_rear_veh]"""
    distance_to_front_vehicles=None
    distance_to_rear_vehicles=None

    def __init__(self,opt=None) -> None:
        if not opt:
            VehicleWrapper.left_front_veh=None
            VehicleWrapper.left_rear_veh=None
            VehicleWrapper.center_front_veh=None
            VehicleWrapper.center_rear_veh=None
            VehicleWrapper.right_front_veh=None
            VehicleWrapper.right_rear_veh=None
            VehicleWrapper.distance_to_front_vehicles=None
            VehicleWrapper.distance_to_rear_vehicles=None
        else:
            if 'left_front_veh' in opt:
                VehicleWrapper.left_front_veh=opt['left_front_veh']
            if 'left_rear_veh' in opt:
                VehicleWrapper.left_rear_veh=opt['left_rear_veh']
            if 'center_front_veh' in opt:
                VehicleWrapper.center_front_veh=opt['center_front_veh']
            if 'center_rear_veh' in opt:
                VehicleWrapper.center_rear_veh=opt['center_rear_veh']
            if 'right_front_veh' in opt:
                VehicleWrapper.right_front_veh=opt['right_front_veh']
            if 'right_rear_veh' in opt:
                VehicleWrapper.right_rear_veh=opt['right_rear_veh']
            if 'dis_to_front_vehs' in opt:
                VehicleWrapper.distance_to_front_vehicles=opt['dis_to_front_vehs']
            if 'dis_to_rear_vehs' in opt:
                VehicleWrapper.distance_to_rear_vehicles=opt['dis_to_rear_vehs']

class Truncated(Enum):
    """Different truncate situations"""
    FALSE=-1
    NORMAL=0
    CHANGE_LANE_IN_LANE_FOLLOW=1

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

class Action(Enum):
    """Parametrized Action for P-DQN"""
    LANE_FOLLOW=0
    LANE_CHANGE_LEFT=-1
    LANE_CHANGE_RIGHT=1
    STOP=2

def process_lane_wp(wps_list, ego_vehicle_z, ego_forward_vector, my_sample_ratio, lane_offset):
    wps = []
    idx = 0

    for wp in wps_list:
        delta_z = wp.transform.location.z - ego_vehicle_z
        yaw_diff = math.degrees(get_yaw_diff(wp.transform.get_forward_vector(), ego_forward_vector))
        yaw_diff = yaw_diff / 90
        if idx % my_sample_ratio == my_sample_ratio-1:
            wps.append([delta_z/3, yaw_diff, lane_offset])
        idx = idx + 1
    return np.array(wps)

def process_veh(ego_vehicle, vehs_info, left_wall, right_wall,vehicle_proximity):
    vehicle_inlane=[vehs_info.left_front_veh,vehs_info.center_front_veh,vehs_info.right_front_veh,
            vehs_info.left_rear_veh,vehs_info.center_rear_veh,vehs_info.right_rear_veh]
    ego_speed = get_speed(ego_vehicle, False)
    ego_location = ego_vehicle.get_location()
    ego_bounding_x = ego_vehicle.bounding_box.extent.x
    ego_bounding_y = ego_vehicle.bounding_box.extent.y
    all_v_info = []
    print('vehicle_inlane: ', vehicle_inlane)
    for i in range(6):
        if i == 0 or i == 3:
            lane = -1
        elif i == 1 or i == 4:
            lane = 0
        else:
            lane = 1
        veh = vehicle_inlane[i]
        wall = False
        if left_wall and (i == 0 or i == 3):
            wall = True
        if right_wall and (i == 2 or i == 5):
            wall = True
        if wall:
            if i < 3:
                v_info = [0.001, 0, lane]
            else:
                v_info = [-0.001, 0, lane]
        else:
            if veh is None:
                if i < 3:
                    v_info = [1, 0, lane]
                else:
                    v_info = [-1, 0, lane]
            else:
                veh_speed = get_speed(veh, False)
                rel_speed = ego_speed - veh_speed

                distance = ego_location.distance(veh.get_location())
                vehicle_len = max(abs(ego_bounding_x), abs(ego_bounding_y)) + \
                    max(abs(veh.bounding_box.extent.x), abs(veh.bounding_box.extent.y))
                distance -= vehicle_len

                if distance < 0:
                    if i < 3:
                        v_info = [0.001, rel_speed, lane]
                    else:
                        v_info = [-0.001, -rel_speed, lane]
                else:
                    if i < 3:
                        v_info = [distance / vehicle_proximity, rel_speed, lane]
                    else:
                        v_info = [-distance / vehicle_proximity, -rel_speed, lane]
        all_v_info.append(v_info)
    # print(all_v_info)
    return np.array(all_v_info)

def process_steer(a_index, steer):
    # left: steering is negative[-1, 0], right: steering is positive[0, 1]
    processed_steer = steer
    if a_index == 0:
        processed_steer = steer * 0.5 - 0.5
    elif a_index == 2:
        processed_steer = steer * 0.5 + 0.5
    return processed_steer

def recover_steer(a_index, steer):
    # recovery [-1, 1] from left change and right change
    recovered_steer=steer
    if a_index==0:
        recovered_steer=(steer+0.5)/0.5
    elif a_index ==2:
        recovered_steer=(steer-0.5)/0.5
    recovered_steer=np.clip(recovered_steer,-1,1)
    return recovered_steer

def fill_action_param(action, steer, throttle_brake, action_param, modify_change_steer):
    if not modify_change_steer:
        action_param[0][action*2] = steer
        action_param[0][action*2+1] = throttle_brake
    else:
        if action == 0:
            steer = np.clip(steer, -1, 0)
            steer = (steer + 0.5) * 2
        elif action == 2:
            steer = np.clip(steer, 0, 1)
            steer = (steer - 0.5) * 2
        action_param[0][action*2] = steer
        action_param[0][action*2+1] = throttle_brake
    return action_param

def ttc_reward(ego_veh,target_veh,min_dis,TTC_THRESHOLD):
    """Caculate the time left before ego vehicle collide with target vehicle"""
    TTC = float('inf')
    if target_veh and ego_veh:
        distance = ego_veh.get_location().distance(target_veh.get_location())
        vehicle_len = max(abs(ego_veh.bounding_box.extent.x),
                            abs(ego_veh.bounding_box.extent.y)) + \
                        max(abs(target_veh.bounding_box.extent.x),
                            abs(target_veh.bounding_box.extent.y))
        distance -= vehicle_len
        if distance < min_dis:
            TTC = 0.01
        else:
            distance -= min_dis
            rel_speed = get_speed(ego_veh,False) - get_speed(target_veh, False)
            if abs(rel_speed) > float(0.0000001):
                TTC = distance / rel_speed
        # print(distance, TTC)
    # fTTC=-math.exp(-TTC)
    if TTC >= 0 and TTC <= TTC_THRESHOLD:
        fTTC = np.clip(np.log(TTC / TTC_THRESHOLD), -1, 0)
    else:
        fTTC = 0

    return fTTC

def comfort(fps, last_acc, acc, last_yaw, yaw):
    acc_jerk = -((acc - last_acc) * fps) ** 2 / ((6 * fps) ** 2)
    yaw_diff = math.degrees(get_yaw_diff(last_yaw, yaw))
    Yaw_jerk = -abs(yaw_diff) / 90
    return np.clip(acc_jerk * 0.5 + Yaw_jerk, -1, 0), yaw_diff

def pdqn_lane_center(lane_center, ego_location):
    def compute(center,ego):
        Lcen=ego.distance(center.transform.location)
        center_yaw=lane_center.transform.get_forward_vector()
        dis=carla.Vector3D(ego.x-lane_center.transform.location.x,
            ego.y-lane_center.transform.location.y,0)
        Lcen*=get_sign(dis,center_yaw)
        return Lcen

    if not test_waypoint(lane_center, True):
        Lcen = 7
        fLcen = -2
        print('lane_center.lane_id, lane_center.road_id, flcen, lane_wid/2: ', lane_center.lane_id,
                lane_center.road_id, fLcen, lane_center.lane_width / 2)
    else:
        Lcen =compute(lane_center,ego_location)
        fLcen = -abs(Lcen)/(lane_center.lane_width/2)
        # if self.current_action == Action.LANE_CHANGE_LEFT and self.current_lane == self.last_lane:
        #     # change left
        #     center_width=lane_center.lane_width
        #     lane_center=lane_center.get_left_lane()
        #     if lane_center is None:
        #         Lcen = 7
        #         fLcen = -2
        #     else:
        #         Lcen =compute(lane_center,ego_location)
        #         fLcen = -abs(Lcen) / (lane_center.lane_width/2+center_width)
        # elif self.current_action == Action.LANE_CHANGE_RIGHT and self.current_lane == self.last_lane:
        #     #change right
        #     center_width=lane_center.lane_width
        #     lane_center=lane_center.get_right_lane()
        #     if lane_center is None:
        #         Lcen = 7
        #         fLcen = -2
        #     else:
        #         Lcen =compute(lane_center,ego_location)
        #         fLcen=-abs(Lcen)/(lane_center.lane_width/2+center_width)
        # else:
        #     #lane follow and stop mode
        #     Lcen =compute(lane_center,ego_location)
        #     fLcen = -abs(Lcen)/(lane_center.lane_width/2)
        #print('pdqn_lane_center: Lcen, fLcen: ', Lcen, fLcen)
    return Lcen, fLcen

def calculate_guide_lane_center(ego_location, lane_center, location, front_distance, rear_distance):
    Lcen = lane_center.transform.location.distance(ego_location)
    # print(
    #     f"Lane Center:{Lcen}, Road ID:{lane_center.road_id}, Lane ID:{lane_center.lane_id}, Yaw:{self.ego_vehicle.get_transform().rotation.yaw}")
    if not test_waypoint(lane_center, True) or Lcen > lane_center.lane_width / 2 + 0.1:
        fLcen = -2
        print('lane_center.lane_id, lcen, flcen: ', lane_center.lane_id, lane_center.road_id, Lcen, fLcen,
                lane_center.lane_width / 2)
    else:
        left = False
        right = False
        if lane_center.lane_id != -1 and front_distance[0] > 20 and front_distance[0]/front_distance[1] > 1.2 and rear_distance[0] > 20:
            left = True
        if lane_center.lane_id != -3 and front_distance[2] > 20 and front_distance[2]/front_distance[1] > 1.2 and rear_distance[2] > 20:
            right = True
        if left:
            Lcen = lane_center.get_left_lane().transform.location.distance(location)
            fLcen = - Lcen / lane_center.lane_width
        elif right:
            Lcen = lane_center.get_right_lane().transform.location.distance(location)
            fLcen = - Lcen / lane_center.lane_width
        else:
            Lcen = lane_center.transform.location.distance(ego_location)
            # print(
            #     f"Lane Center:{Lcen}, Road ID:{lane_center.road_id}, Lane ID:{lane_center.lane_id}, Yaw:{self.ego_vehicle.get_transform().rotation.yaw}")
            if not test_waypoint(lane_center, True) or Lcen > lane_center.lane_width / 2 + 0.1:
                fLcen = -2
                print('lane_center.lane_id, lcen, flcen: ', lane_center.lane_id, lane_center.road_id, Lcen, fLcen, lane_center.lane_width / 2)
            else:
                fLcen = - Lcen / (lane_center.lane_width / 2)
    return Lcen, fLcen

