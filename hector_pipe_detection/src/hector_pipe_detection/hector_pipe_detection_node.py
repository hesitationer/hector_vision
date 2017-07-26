#!/usr/bin/env python
from __future__ import division, print_function
import rospy
import sensor_msgs.msg
import geometry_msgs.msg
import std_msgs.msg
import image_geometry
import tf2_ros

import cv2
import cv_bridge
import numpy as np


def low_pass_filter(old, new, factor):
    return (1 - factor) * old + factor * new





class PipeDetectionNode:
    def __init__(self):
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)

        self.cv_bridge = cv_bridge.CvBridge()
        self.circle_pos = None
        self.circle_radius = rospy.get_param("circle_radius", 0.025)

        self.camera_info = None
        self.camera_info_sub = rospy.Subscriber("camera_info", sensor_msgs.msg.CameraInfo, self.camera_info_cb)

        self.model = None
        self.meter_to_pixel_ratio = None
        if self.wait_for_camera_info(5):
            self.model = image_geometry.PinholeCameraModel()
            self.model.fromCameraInfo(self.camera_info)
            self.calc_m_to_pixel_ratio(self.model)

        self.image_sub = rospy.Subscriber("image", sensor_msgs.msg.Image, self.image_cb)

        self.enabled_status_pub = rospy.Publisher("~enabled_status", std_msgs.msg.Bool, queue_size=100, latch=True)
        self.enabled_sub = rospy.Subscriber("~enabled", std_msgs.msg.Bool, self.enabled_cb)
        self._enabled = False
        self.enabled = rospy.get_param("~enabled", False)
        self.publish_enabled_status()

        self.circle_image_pub = rospy.Publisher("~detected_circle", sensor_msgs.msg.Image, queue_size=100, latch=False)
        self.detected_pose_pub = rospy.Publisher("~detected_pose", geometry_msgs.msg.PoseStamped, queue_size=100, latch=False)

    def wait_for_camera_info(self, timeout):
        rospy.loginfo("Waiting for camera_info")
        start = rospy.Time.now()
        rate = rospy.Rate(1)
        while self.camera_info is None:
            now = rospy.Time.now()
            if (now - start).to_sec() > timeout:
                rospy.logwarn("Timed out waiting for camera_info")
                return False
            else:
                rate.sleep()
        return True


    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = value
        self.publish_enabled_status()

    def image_cb(self, image_msg):
        if self.enabled:
            self.detect_pipe(image_msg)

    def camera_info_cb(self, camera_info_msg):
        self.camera_info = camera_info_msg

    def enabled_cb(self, bool_msg):
        self.enabled = bool_msg.data
        self.publish_enabled_status()

    def publish_enabled_status(self):
        status = "Enabled" if self.enabled else "Disabled"
        rospy.loginfo(status + " pipe detection.")
        self.enabled_status_pub.publish(std_msgs.msg.Bool(self._enabled))

    def detect_pipe(self, image):
        cv_image = self.cv_bridge.imgmsg_to_cv2(image)
        #cv2.imwrite("pipe_image.png", cv_image)
        image_gray = cv2.cvtColor(cv_image, cv2.COLOR_RGB2GRAY)
        blurred = cv2.blur(image_gray, (10, 10))
        circles = cv2.HoughCircles(blurred, cv2.HOUGH_GRADIENT, 1, 1000, param1=100, param2=50, minRadius=10,
                                   maxRadius=0)
        if circles is None:
            return False

        circles = np.array(circles[0])
        if self.circle_pos is None:
            self.circle_pos = circles[0]
        else:
            self.circle_pos = low_pass_filter(self.circle_pos, circles[0], 0.1)

        circle_image = cv_image.copy()
        cv2.circle(circle_image, (self.circle_pos[0], self.circle_pos[1]), self.circle_pos[2], (0, 255, 0), 2)  # draw the outer circle
        cv2.circle(circle_image, (self.circle_pos[0], self.circle_pos[1]), 2, (0, 0, 255), 3)  # draw the center of the circle
        self.circle_image_pub.publish(self.cv_bridge.cv2_to_imgmsg(circle_image, encoding="rgb8"))

        if self.model is None:
            rospy.logwarn("No camera info received. Can't use model.")
            return False

        distance = self.meter_to_pixel(self.circle_radius) / self.circle_pos[2]
        print("distance: ", distance)
        ray = np.array(self.model.projectPixelTo3dRay(self.circle_pos[0:2]))
        point = distance * ray / np.linalg.norm(ray)

        self.publish_pose(image, point)
        return True

    def calc_m_to_pixel_ratio(self, model):
        """

        :type model: image_geometry.PinholeCameraModel
        """
        x1 = np.array([0.5, 0, 1])
        x2 = np.array([-0.5, 0, 1])

        p1 = np.array(model.project3dToPixel(x1))
        p2 = np.array(model.project3dToPixel(x2))

        self.meter_to_pixel_ratio = np.linalg.norm(p1 - p2)

    def meter_to_pixel(self, meter):
        if self.meter_to_pixel_ratio is not None:
            return self.meter_to_pixel_ratio * meter
        else:
            rospy.logwarn("meter_to_pixel_ratio hasn't been calculated yes")
            return 0

    def pixel_to_meter(self, pixel):
        if self.meter_to_pixel_ratio is not None:
            return pixel / self.meter_to_pixel_ratio
        else:
            rospy.logwarn("meter_to_pixel_ratio hasn't been calculated yes")
            return 0

    def publish_pose(self, image, ray):
        pose_msg = geometry_msgs.msg.PoseStamped()
        pose_msg.header.stamp = image.header.stamp
        pose_msg.header.frame_id = image.header.frame_id
        pose_msg.pose.position.x = ray[0]
        pose_msg.pose.position.y = ray[1]
        pose_msg.pose.position.z = ray[2]

        try:
            transform = self.tf_buffer.lookup_transform("base_link", image.header.frame_id, image.header.stamp)
        except Exception as e:
            rospy.logwarn_throttle(5, "TF Exception: " + str(e))
        else:
            pose_msg.pose.orientation = transform.transform.rotation
        self.detected_pose_pub.publish(pose_msg)


if __name__ == "__main__":
    rospy.init_node("pipe_detection_node")
    pipe_detection_node = PipeDetectionNode()
    rospy.spin()
