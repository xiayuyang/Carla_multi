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