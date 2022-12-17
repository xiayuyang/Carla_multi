import math
import numpy as np
from gym_carla.env.util.misc import get_speed,get_yaw_diff

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

    def __init__(self,opt=None) -> None:
        if not opt:
            VehicleWrapper.left_front_veh=None
            VehicleWrapper.left_rear_veh=None
            VehicleWrapper.center_front_veh=None
            VehicleWrapper.center_rear_veh=None
            VehicleWrapper.right_front_veh=None
            VehicleWrapper.right_rear_veh=None
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

def process_veh(ego_vehicle, vehicle_inlane, left_wall, right_wall,vehicle_proximity):
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

def process_action(a_index, steer):
    # left: steering is negative[-1, 0], right: steering is positive[0, 1]
    processed_steer = steer
    if a_index == 0:
        processed_steer = steer * 0.5 - 0.5
    elif a_index == 2:
        processed_steer = steer * 0.5 + 0.5
    return processed_steer

def recovery_action(action, action_param):
    # recovery [-1, 1] from left change and right change
    steer = action_param[2*action]
    if action == 0:
        steer = np.clip(steer, -1, 0.1)
        steer = (steer + 0.5) * 2
    elif action == 2:
        steer = np.clip(steer, -0.1, 1)
        steer = (steer - 0.5) * 2
    return steer

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