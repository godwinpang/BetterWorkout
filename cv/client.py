import edgeiq
import cv2
import socketio
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
                'http://localhost:5000', namespaces=['/cv'])
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

    def send_notify_db_update(self):
        self._sio.emit('cv-cmd', {'cmd': 'notify_db_update'})

    def send_update_camera_stats(self, fps, inf_time):
        self._sio.emit(
                'cv-cmd',
                {
                    'cmd': 'update_camera_stats',
                    'fps': fps,
                    'inf_time': inf_time
                    })

    def close(self):
        self._sio.disconnect()
