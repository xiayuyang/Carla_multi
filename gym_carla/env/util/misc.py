""" Module with auxiliary functions. """
import logging
import math
import numpy as np
import carla
from gym_carla.env.settings import *
from enum import Enum


class RoadOption(Enum):
    """
    RoadOption represents the possible topological configurations when moving from a segment of lane to other.

    """
    VOID = -1
    LEFT = 1
    RIGHT = 2
    STRAIGHT = 3
    LANEFOLLOW = 4
    CHANGELANELEFT = 5
    CHANGELANERIGHT = 6

def remove_unnecessary_objects(world):
    """Remove unuseful objects in the world"""
    def remove_object(world,objs,obj):
        for ob in world.get_environment_objects(obj):
            objs.add(ob.id)
    world.unload_map_layer(carla.MapLayer.StreetLights)
    world.unload_map_layer(carla.MapLayer.Buildings)
    world.unload_map_layer(carla.MapLayer.Decals)
    world.unload_map_layer(carla.MapLayer.Walls)
    world.unload_map_layer(carla.MapLayer.Foliage)
    world.unload_map_layer(carla.MapLayer.ParkedVehicles)
    # world.unload_map_layer(carla.MapLayer.Particles)
    world.unload_map_layer(carla.MapLayer.Ground)
    labels=[carla.CityObjectLabel.TrafficSigns,carla.CityObjectLabel.TrafficLight,carla.CityObjectLabel.Other,
        carla.CityObjectLabel.Poles, carla.CityObjectLabel.Static,carla.CityObjectLabel.Dynamic,carla.CityObjectLabel.Buildings,
        carla.CityObjectLabel.Fences, carla.CityObjectLabel.Walls,carla.CityObjectLabel.Vegetation,carla.CityObjectLabel.Ground]
    objs = set()
    for label in labels:
        for obj in world.get_environment_objects(label):
            objs.add(obj.id)
    world.enable_environment_objects(objs, False)
    # world.unload_map_layer(carla.MapLayer.Props)

def test_waypoint(waypoint, ego=False):
    """
    test if a given waypoint is on chosen route
    :param reward: 
        True means this function is used in get_reward function,
            since there are some defects in OpenDrive file to determine whether ego vehicle drive out of chosen route
        False means this function is used elswhere in Carla Env
    """
    # if (waypoint.road_id in STRAIGHT or waypoint.road_id in JUNCTION) and waypoint.lane_id == -1:
    #     return True
    # if waypoint.road_id in CURVE and waypoint.lane_id == 1:
    #     return True
    if not ego:
        if (waypoint.road_id in ROADS or waypoint.road_id in DISTURB_ROADS) and (waypoint.lane_id == -1 or waypoint.lane_id == -2 or waypoint.lane_id == -3):
            return True
        return False
    else:
        if waypoint.road_id in ROADS and (waypoint.lane_id == -1 or waypoint.lane_id == -2 or waypoint.lane_id == -3):
            return True
        return False


def draw_waypoints(world, waypoints, life_time=0.0, z=0.5):
    """
    Draw a list of waypoints at a certain height given in z.

        :param world: carla.world object
        :param waypoints: list or iterable container with the waypoints to draw
        :param z: height in meters
    """
    for wpt in waypoints:
        wpt_t = wpt.transform
        begin = wpt_t.location + carla.Location(z=z)
        angle = math.radians(wpt_t.rotation.yaw)
        end = begin + carla.Location(x=math.cos(angle), y=math.sin(angle))
        world.debug.draw_arrow(begin, end, arrow_size=0.3, life_time=life_time)

def fill_action_param(action, steer, throttle_brake, action_param):
    action_param[0][action*2] = steer
    action_param[0][action*2+1] = throttle_brake
    return action_param

def _retrieve_options(list_waypoints, current_waypoint):
    """
    Compute the type of connection between the current active waypoint and the multiple waypoints present in
    list_waypoints. The result is encoded as a list of RoadOption enums.

    :param list_waypoints: list with the possible target waypoints in case of multiple options
    :param current_waypoint: current active waypoint
    :return: list of RoadOption enums representing the type of connection from the active waypoint to each
        candidate in list_waypoints
    """
    options = []
    for next_waypoint in list_waypoints:
        # this is needed because something we are linking to
        # the beggining of an intersection, therefore the
        # variation in angle is small
        next_next_waypoint = next_waypoint.next(3.0)[0]
        link = _compute_connection(current_waypoint, next_next_waypoint)
        options.append(link)

    return options

def _compute_connection(current_waypoint, next_waypoint):
    """
    Compute the type of topological connection between an active waypoint (current_waypoint) and a target waypoint
    (next_waypoint).

    :param current_waypoint: active waypoint
    :param next_waypoint: target waypoint
    :return: the type of topological connection encoded as a RoadOption enum:
        RoadOption.STRAIGHT
        RoadOption.LEFT
        RoadOption.RIGHT
    """
    n = next_waypoint.transform.rotation.yaw
    n = n % 360.0

    c = current_waypoint.transform.rotation.yaw
    c = c % 360.0

    diff_angle = (n - c) % 180.0
    if diff_angle < 1.0:
        return RoadOption.STRAIGHT
    elif diff_angle > 90.0:
        return RoadOption.LEFT
    else:
        return RoadOption.RIGHT

def _compute_next_waypoints(k, last_waypoint, direction, sampling_resolution, shoulder_lane_id):
    # ensure waypoint is in the shoulder
    for _ in range(k):
        if direction:
            next_waypoints = list(last_waypoint.next(sampling_resolution))
        else:
            next_waypoints = list(last_waypoint.previous(sampling_resolution))

        if len(next_waypoints) == 0:
            break
        elif len(next_waypoints) == 1:
            # only one option available ==> lanefollowing
            next_waypoint = next_waypoints[0]
            road_option = RoadOption.LANEFOLLOW
        else:
            road_options_list = self._retrieve_options(
                next_waypoints, last_waypoint)

            # # random choice between the possible options
            # road_option = road_options_list[1]
            # #road_option = random.choice(road_options_list)
            # next_waypoint = next_waypoints[road_options_list.index(road_option)]

            idx = None
            for i, wp in enumerate(next_waypoints):
                if wp.lane_id == shoulder_lane_id:
                    next_waypoint = wp
                    idx = i
    return next_waypoint

def get_lane_center_pre(map, location):
    lane_center = map.get_waypoint(location, project_to_road=True, lane_type=carla.LaneType.Driving)
    road_id = lane_center.road_id
    lane_id = lane_center.lane_id
    print('before process road_id and lane_id: ', road_id, lane_id)
    in_road = road_id in ROADS
    sample_re = 10
    if road_id in FORWARD_ROADS:
        lane_shoulder = map.get_waypoint(location, project_to_road=True, lane_type=carla.LaneType.Shoulder)
        print('lane_should: ', lane_shoulder.road_id, lane_shoulder.lane_id)
        lane_shoulder_center = _compute_next_waypoints(1, lane_shoulder, True, sample_re, lane_shoulder.lane_id)
        lane_minus_1 = lane_shoulder_center.get_right_lane()
        if lane_minus_1 is not None:
            print('RIGHT, lane_minus_1: ', lane_minus_1.lane_id, lane_minus_1.road_id)
        lane_minus_1 = lane_shoulder_center.get_left_lane()
        if lane_minus_1 is not None:
            print('LEFT, lane_minus_1: ', lane_minus_1.lane_id, lane_minus_1.road_id)
        lane_center = _compute_next_waypoints(1, lane_minus_1, False, sample_re, lane_minus_1.lane_id)
    elif road_id in BACKWARD_ROADS:
        lane_shoulder = map.get_waypoint(location, project_to_road=True, lane_type=carla.LaneType.Shoulder)
        lane_shoulder_center = _compute_next_waypoints(1, lane_shoulder, False, sample_re, lane_shoulder.lane_id)
        lane_minus_1 = lane_shoulder_center.get_right_lane()
        if lane_minus_1 is not None:
            print('RIGHT, lane_minus_1: ', lane_minus_1.lane_id, lane_minus_1.road_id)
        lane_minus_1 = lane_shoulder_center.get_left_lane()
        if lane_minus_1 is not None:
            print('LEFT, lane_minus_1: ', lane_minus_1.lane_id, lane_minus_1.road_id)
        lane_center = _compute_next_waypoints(1, lane_minus_1, True, sample_re, lane_minus_1.lane_id)

    print(lane_center.road_id, lane_center.lane_id)
    return lane_center

def get_lane_center(map, location):
    """Project current loction to its lane center, return lane center waypoint"""
    # test code for junction lane invasion bug
    # if lane_center.is_junction:
    #     test=self.map.get_waypoint(self.ego_vehicle.get_location(),project_to_road=True,lane_type=carla.LaneType.Shoulder)
    #     test_l=test.get_left_lane()
    #     print(lane_center.road_id,lane_center.lane_id,test.road_id,
    #         test.lane_id,test_l.road_id,test_l.lane_id,
    #         lane_center.transform.location,test_l.transform.location,sep='\t')
    # lane_center=map.get_waypoint(location,project_to_road=True)
    # if lane_center.is_junction:
    #     """If ego vehicle is in junction, to avoid bug in get_waypoint function,
    #     first project the ego vehicle location to the road shoulder lane, then get the straight lane waypoint
    #     according to the relative location between shoulder lane and current driving lane"""
    #     shoulder = map.get_waypoint(location, project_to_road=True,lane_type=carla.LaneType.Shoulder)
    #     lane_center = shoulder.get_left_lane()

    lane_center = map.get_waypoint(location, project_to_road=True, lane_type=carla.LaneType.Driving)
    road_id = lane_center.road_id
    lane_id = lane_center.lane_id
    # print('before process road_id and lane_id: ', road_id, lane_id)
    in_road = road_id in ROADS

    if road_id in DISTURB_ROADS:
        # in a left+straight, get_right_lane = None, for example: road 2039 in Town05
        # """
        #     if ego vehicle not in the specific roads, we first get the right waypoint of lanecenter
        #     and then get the left waypoint of the right waypoint
        #     finally, check lanecenter whether is in lane -1
        #     if != -1, it indicate lanecenter in shoulder, thus get the right waypoint
        # """
        # print('in_road: ', location, lane_center, in_road, road_id, ROADS)
        # right = lane_center.get_right_lane()
        # lane_center = right.get_left_lane()
        # if lane_center.lane_id != -1:
        #     lane_center = lane_center.get_right_lane()
        lane_shoulder = map.get_waypoint(location, project_to_road=True, lane_type=carla.LaneType.Shoulder)
        # print('lane_shoulder, lane_shoulder.lane_id, road_id, lane_id: ', lane_shoulder, lane_shoulder.lane_id, road_id, lane_id)
        if lane_shoulder.lane_id == 1:
            lane_center_right = lane_shoulder.get_right_lane()
            lane_center_left = lane_shoulder.get_left_lane()
            # if lane_center_right is not None:
            #     print('lane_center_right', lane_center_right, lane_center_right.lane_id)
            # else:
            #     print('right is None')
            # if lane_center_left is not None:
            #     print('lane_center_left', lane_center_left, lane_center_left.lane_id)
            # else:
            #     print('left is None')
            if (lane_center_right is None or lane_center_right.lane_id != -1) and (lane_center_left is None or lane_center_left.lane_id != -1):
                logging.error('get lane error!!')
                lane_Sidewalk = map.get_waypoint(location, project_to_road=True, lane_type=carla.LaneType.Sidewalk)
                if lane_Sidewalk.lane_id == -5:
                    # print('lane_shoulder.lane_id == -5')
                    lane_center = lane_Sidewalk.get_left_lane().get_left_lane().get_left_lane().get_left_lane()
                elif lane_Sidewalk.lane_id == -6:
                    # print('lane_shoulder.lane_id == -6')
                    lane_center = lane_Sidewalk.get_left_lane().get_left_lane().get_left_lane().get_left_lane().get_left_lane()
            elif lane_center_left is not None and lane_center_left.lane_id == -1:
                lane_center = lane_center_left
            elif lane_center_right is not None and lane_center_right.lane_id == -1:
                lane_center = lane_center_right
        elif lane_shoulder.lane_id == -5:
            # print('lane_shoulder.lane_id == -5')
            lane_center = lane_shoulder.get_left_lane().get_left_lane().get_left_lane().get_left_lane()
        elif lane_shoulder.lane_id == -6:
            # print('lane_shoulder.lane_id == -6')
            lane_center = lane_shoulder.get_left_lane().get_left_lane().get_left_lane().get_left_lane().get_left_lane()
    # print('lane_center.road_id: ', lane_center.road_id)
    return lane_center

# def get_yaw_diff(rotation1, rotation2):
#     if abs(rotation1.yaw - rotation2.yaw) < 90:
#         yaw_diff = rotation1.yaw - rotation2.yaw
#     else:
#         yaw_diff = (rotation1.yaw + 720) % 360 - (rotation2.yaw + 720) % 360
#     if abs(yaw_diff) < 90:
#         yaw_diff /= 90
#     else:
#         # The current state is not stable, deprecate it
#         yaw_diff = np.sign(yaw_diff)
#
#     return yaw_diff
def get_yaw_diff(vector1,vector2):
    """
    Get two vectors' yaw difference in radians (0-PI), and
    negative value means vector1 is on the left of vector2, positive is on the right of vector2.
    The vector format should be carla.Vector3D, and we set the vectors' z value to 0 because of the map been working on.
    """
    vector1.z = 0
    vector2.z = 0
    # vector1=vector1.make_unit_vector()
    # vector2=vector2.make_unit_vector()
    theta_sign=1 if vector1.cross(vector2).z >= 0 else -1
    if vector1.length() != 0.0 and vector2.length() != 0.0:
        theta = math.acos(
            np.clip(vector1.dot(vector2)/(vector1.length()*vector2.length()), -1, 1))
    else:
        theta = 0
    return theta_sign*theta

def get_speed(vehicle, unit=True):
    """
    Compute speed of a vehicle, ignore z value
        :param unit: the unit of return, True means Km/h, False means m/s
        :param vehicle: the vehicle for which speed is calculated
        :return: speed as a float 
    """
    vel = vehicle.get_velocity()

    if unit:
        return 3.6 * math.sqrt(vel.x ** 2 + vel.y ** 2)
    else:
        return math.sqrt(vel.x ** 2 + vel.y ** 2)


def get_acceleration(vehicle, unit=True):
    """
    Compute acceleration of a vehicle
        :param unit: the unit of return, True means Km/h^2, False means m/s^2
        :param vehicle: the vehicle for which speed is calculated
        :return: acceleration as a float 
    """
    acc = vehicle.get_acceleration()

    if unit:
        return 36 * 36 * 10 * math.sqrt(acc.x ** 2 + acc.y ** 2)
    else:
        return math.sqrt(acc.x ** 2 + acc.y ** 2)


def get_actor_polygons(world, filt):
    """Get the bounding box polygon of actors.
    Args:
        filt: the filter indicating what type of actors we'll look at.
        world: carla.world
    Returns:
        actor_poly_dict: a dictionary containing the bounding boxes of specific actors.
    """
    actor_poly_dict = {}
    for actor in world.get_actors().filter(filt):
        # Get x, y and yaw of the actor
        trans = actor.get_transform()
        x = trans.location.x
        y = trans.location.y
        yaw = trans.rotation.yaw / 180 * np.pi
        # Get length and width
        bb = actor.bounding_box
        l = bb.extent.x
        w = bb.extent.y
        # Get bounding box polygon in the actor's local coordinate
        poly_local = np.array([[l, w], [l, -w], [-l, -w], [-l, w]]).transpose()
        # Get rotation matrix to transform to global coordinate
        R = np.array([[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]])
        # Get global bounding box polygon
        poly = np.matmul(R, poly_local).transpose() + np.repeat([[x, y]], 4, axis=0)
        actor_poly_dict[actor.id] = poly
    return actor_poly_dict


def get_trafficlight_trigger_location(traffic_light):
    """
    Calculates the yaw of the waypoint that represents the trigger volume of the traffic light
    """

    def rotate_point(point, radians):
        """
        rotate a given point by a given angle
        """
        rotated_x = math.cos(radians) * point.x - math.sin(radians) * point.y
        rotated_y = math.sin(radians) * point.x - math.cos(radians) * point.y

        return carla.Vector3D(rotated_x, rotated_y, point.z)

    base_transform = traffic_light.get_transform()
    base_rot = base_transform.rotation.yaw
    area_loc = base_transform.transform(traffic_light.trigger_volume.location)
    area_ext = traffic_light.trigger_volume.extent

    point = rotate_point(carla.Vector3D(0, 0, area_ext.z), math.radians(base_rot))
    point_location = area_loc + carla.Location(x=point.x, y=point.y)

    return carla.Location(point_location.x, point_location.y, point_location.z)


def is_within_distance(target_transform, reference_transform, max_distance, angle_interval=None):
    """
    Check if a location is both within a certain distance from a reference object.
    By using 'angle_interval', the angle between the location and reference transform
    will also be taken into account, being 0 a location in front and 180, one behind.

    :param target_transform: location of the target object
    :param reference_transform: location of the reference object
    :param max_distance: maximum allowed distance
    :param angle_interval: only locations between [min, max] angles will be considered. This isn't checked by default.
    :return: boolean
    """
    target_vector = np.array([
        target_transform.location.x - reference_transform.location.x,
        target_transform.location.y - reference_transform.location.y
    ])
    norm_target = np.linalg.norm(target_vector)

    # If the vector is too short, we can simply stop here
    if norm_target < 0.001:
        return True

    # Further than the max distance
    if norm_target > max_distance:
        return False

    # We don't care about the angle, nothing else to check
    if not angle_interval:
        return True

    min_angle = angle_interval[0]
    max_angle = angle_interval[1]

    fwd = reference_transform.get_forward_vector()
    forward_vector = np.array([fwd.x, fwd.y])
    angle = math.degrees(math.acos(np.clip(np.dot(forward_vector, target_vector) / norm_target, -1., 1.)))

    return min_angle < angle < max_angle


def is_within_distance_ahead(target_location, current_location, current_transform, max_distance):
    """
      Check if a target object is within a certain distance in front of a reference object.

      :param target_location: location of the target object
      :param current_location: location of the reference object
      :param current_transform: transform of the reference object
      :param max_distance: maximum allowed distance
      :return: True if target object is within max_distance ahead of the reference object
    """
    target_vector = np.array([target_location.x - current_location.x, target_location.y - current_location.y])
    norm_target = np.linalg.norm(target_vector)
    if norm_target < 0.001:
        return True
    if norm_target > max_distance:
        return False

    # forward_vector = np.array(
    #     [math.cos(math.radians(orientation)), math.sin(math.radians(orientation))])
    fwd = current_transform.get_forward_vector()
    forward_vector = np.array([fwd.x, fwd.y])
    d_angle = math.degrees(math.acos(
        np.clip(np.dot(forward_vector, target_vector) / norm_target, -1, 1)))

    return 0.0 < d_angle < 90.0


def is_within_distance_rear(target_location, current_location, current_transform, max_distance):
    """
      Check if a target object is within a certain distance in front of a reference object.

      :param target_location: location of the target object
      :param current_location: location of the reference object
      :param current_transform: transform of the reference object
      :param max_distance: maximum allowed distance
      :return: True if target object is within max_distance ahead of the reference object
    """
    target_vector = np.array([target_location.x - current_location.x, target_location.y - current_location.y])
    norm_target = np.linalg.norm(target_vector)
    if norm_target < 0.001:
        return True
    if norm_target > max_distance:
        return False

    # forward_vector = np.array(
    #     [math.cos(math.radians(orientation)), math.sin(math.radians(orientation))])
    fwd = current_transform.get_forward_vector()
    forward_vector = np.array([fwd.x, fwd.y])
    d_angle = math.degrees(math.acos(
        np.clip(np.dot(forward_vector, target_vector) / norm_target, -1, 1)))

    return 90.0 < d_angle < 180.0


def compute_magnitude_angle(target_location, current_location, orientation):
    """
    Compute relative angle and distance between a target_location and a current_location

        :param target_location: location of the target object
        :param current_location: location of the reference object
        :param orientation: orientation of the reference object
        :return: a tuple composed by the distance to the object and the angle between both objects
    """
    target_vector = np.array([target_location.x - current_location.x, target_location.y - current_location.y])
    norm_target = np.linalg.norm(target_vector)

    forward_vector = np.array([math.cos(math.radians(orientation)), math.sin(math.radians(orientation))])
    d_angle = math.degrees(math.acos(np.clip(np.dot(forward_vector, target_vector) / norm_target, -1., 1.)))

    return (norm_target, d_angle)


def vector(location_1, location_2):
    """
    Returns the unit vector from location_1 to location_2

        :param location_1, location_2: carla.Location objects
    """
    x = location_2.x - location_1.x
    y = location_2.y - location_1.y
    z = location_2.z - location_1.z
    norm = np.linalg.norm([x, y, z]) + np.finfo(float).eps

    return [x / norm, y / norm, z / norm]


def compute_distance(location_1, location_2):
    """
    Euclidean distance between 3D points
    This map's z value does not change, so ignore the location's z value
        :param location_1, location_2: 3D points
    """
    x = location_2.x - location_1.x
    y = location_2.y - location_1.y
    z = location_2.z - location_1.z
    norm = np.linalg.norm([x, y, z]) + np.finfo(float).eps
    return norm


def positive(num):
    """
    Return the given number if positive, else 0

        :param num: value to check
    """
    return num if num > 0.0 else 0.0
