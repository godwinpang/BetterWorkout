import time
import edgeiq
import socketio
import os
import cv2
import base64
import time

class ServerComm:
    def __init__(self):
        self._sio = socketio.Client()
        self._max_image_width = 640
        self._max_image_height = 480

    def setup(self):
        print('[INFO] Connecting to server...')
        self._sio.connect(
                'http://10.19.191.50:5000', namespaces=['/cv'])
        print('[INFO] Successfully connected to server.')
        time.sleep(1)

    def send_frame(self, frame):
        frame = edgeiq.resize(
                frame, width=self._max_image_width,
                height=self._max_image_height, keep_scale=True)

        # Encode frame as jpeg
        frame = cv2.imencode('.jpg', frame)[1].tobytes()
        # Encode frame in base64 representation and remove
        # utf-8 encoding
        frame = base64.b64encode(frame).decode('utf-8')
        frame = "data:image/jpeg;base64,{}".format(frame)
        self._sio.emit('cv-cmd', {'cmd': 'update_frame', 'data': frame})

    def close(self):
        self._sio.disconnect()


def main():
    pose_estimator = edgeiq.PoseEstimation("alwaysai/human-pose")
    pose_estimator.load(
            engine=edgeiq.Engine.DNN_OPENVINO,
            accelerator=edgeiq.Accelerator.MYRIAD)

    print("Loaded model:\n{}\n".format(pose_estimator.model_id))
    print("Engine: {}".format(pose_estimator.engine))
    print("Accelerator: {}\n".format(pose_estimator.accelerator))

    fps = edgeiq.FPS()
    server_comm = ServerComm()
    server_comm.setup()

    try:
        with edgeiq.WebcamVideoStream(cam=0) as video_stream:
            time.sleep(2.0)
            fps.start()
            while True:
                frame = video_stream.read()
                server_comm.send_frame(frame)
                fps.update()

    finally:
        fps.stop()

if __name__ == "__main__":
    main()
