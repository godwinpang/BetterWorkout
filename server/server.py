import os
import socket
from flask_socketio import SocketIO
from flask import Flask, render_template, request
import queue
import traceback
import eventlet
import threading
import database
import collections
import time


eventlet.monkey_patch()


_FLASK_APP_ROOT_PATH = os.path.dirname(__file__)
_app = Flask(
        __name__, root_path=_FLASK_APP_ROOT_PATH,
        static_url_path='/data', static_folder='data')
_socketio = SocketIO(_app)

DEST_WEB_STREAM = {
        'msg-name': 'web-data',
        'namespace': '/web-stream',
        'msgs': [
            'update_frame',
            'update_camera_stats'
            ]
        }

DEST_WEB_CONTENT = {
        'msg-name': 'web-data',
        'namespace': '/web-content',
        'msgs': [
            'notify_db_update',
            'update_text',
            'update_status'
            ]
        }

DEST_SERVER = {}

DEST_CVAPP = {}


class _RxThread(threading.Thread):
    """Monitor the Rx queue for received messages."""
    def __init__(
            self, rx_queue, tx_queue, exit_event, error_queue):
        self._rx_queue = rx_queue
        self._tx_queue = tx_queue
        self._exit_event = exit_event
        self._error_queue = error_queue
        super(_RxThread, self).__init__()

    def _query_db(self):
        with database.Database() as db:
            videos = db.get_all(organize_by_date=True)

        self._tx_queue.put({'cmd': 'update_text', 'data': videos})

    def _delete_event(self, event_id):
        with database.Database() as db:
            video = db.get_for_id(event_id)
            db.delete_by_id(event_id)

        # Once database delete completes, delete the video file
        filename = os.path.basename(video['path'])
        path = os.path.join(database.VIDEO_DIR, filename)
        os.remove(path)
        print('Removed {}'.format(filename))

    def _rx_msg(self):
        while True:
            data = self._rx_queue.get()

            if data['cmd'] == 'stop':
                self._exit_event.set()
                break
            elif data['cmd'] == 'query_db':
                self._query_db()
            elif data['cmd'] == 'delete':
                self._delete_event(data['data'])

    def run(self):
        try:
            self._rx_msg()
        except Exception as e:
            tb = traceback.format_exc()
            self._error_queue.put((e, tb))
            raise e


class _TxThread(threading.Thread):
    """Monitor the Tx queue and send messages to clients."""
    def __init__(
            self, tx_queue, msg_name, namespace, inter_msg_time,
            error_queue):
        self._tx_queue = tx_queue
        self._msg_name = msg_name
        self._namespace = namespace
        self._inter_msg_time = inter_msg_time
        self._error_queue = error_queue
        super(_TxThread, self).__init__()

    def _tx_msg(self):
        while True:
            data = self._tx_queue.get()
            _socketio.emit(
                    self._msg_name, data, namespace=self._namespace)
            # Sleep is required for message to be sent
            _socketio.sleep(self._inter_msg_time)

    def run(self):
        try:
            self._tx_msg()
        except Exception as e:
            tb = traceback.format_exc()
            self._error_queue.put((e, tb))
            raise e


class ConnectionMgr:
    def __init__(self):
        self._connections = {
                '/web-content': [],
                '/web-stream': [],
                '/cv': []
                }
        # Mgr will potentially be used across mutliple threads
        self._lock = threading.Lock()

    def add_connection(self, namespace, client_id):
        with self._lock:
            c = self._connections.get(namespace, None)
            if c is None:
                raise AssertionError('Unknown namespace! {}'.format(namespace))

            c.append(client_id)
        print('[INFO] Client connected: {}:{}'.format(
                    namespace, client_id))

    def remove_connection(self, namespace, client_id):
        with self._lock:
            c = self._connections.get(namespace, None)
            if c is None:
                raise AssertionError('Unknown namespace! {}'.format(namespace))

            if client_id in c:
                c.remove(client_id)
            else:
                print('[WARNING] Removing unknown client!')

        print('[INFO] Client disconnected: {}:{}'.format(
                    namespace, client_id))

    def get_num_connections(self, namespace):
        with self._lock:
            c = self._connections.get(namespace, None)
            if c is None:
                raise AssertionError('Unknown namespace! {}'.format(namespace))

            return len(c)


class CircularQueue:
    """Thread-safe circular queue using a deque."""
    def __init__(self, max_size=None):
        self._queue = collections.deque(maxlen=max_size)

    def put(self, item):
        self._queue.appendleft(item)

    def get(self):
        while True:
            try:
                return self._queue.pop()
            except IndexError:
                time.sleep(0.01)


class WebInterface(object):
    """
    Host a video and data streaming server on the device.

    :type queue_depth: integer
    :param queue_depth: The depth of the data queue used for communication
                        between the application process and server process.
    :type inter_msg_time: float
    :param inter_msg_time: The time in seconds between message sends to
                           attached clients.
    :type drop_frames: boolean
    :param drop_frames: Indicates whether :func:`~send_data()` should block
                        when queue is full or drop frames.
    """
    def __init__(
            self, queue_depth=2, inter_msg_time=0, drop_frames=True):
        # Bind to all interfaces
        self._ipaddr = '0.0.0.0'
        self._port = 5000
        self._queue_depth = queue_depth
        self._inter_msg_time = inter_msg_time
        self._drop_frames = drop_frames

        # Rx and Tx queues are used only by server process
        self._content_tx_queue = queue.Queue()
        self._stream_tx_queue = CircularQueue(2)
        self._rx_queue = queue.Queue()

        # Error queue is shared between server process and CV process
        self._error_queue_depth = 5
        self._error_queue = queue.Queue(self._error_queue_depth)

        # Exit event is used only in server process
        self._exit_event = threading.Event()

        self._rx_thread = _RxThread(
                self._rx_queue, self._content_tx_queue, self._exit_event,
                self._error_queue)

    def setup(self):
        """Setup and start the web server."""
        self._rx_thread.start()

        _app.config['CONTENT_TX_QUEUE'] = self._content_tx_queue
        _app.config['CONTENT_TX_THREAD'] = _TxThread(
                self._content_tx_queue, DEST_WEB_CONTENT['msg-name'],
                DEST_WEB_CONTENT['namespace'], self._inter_msg_time,
                self._error_queue)

        _app.config['STREAM_TX_QUEUE'] = self._stream_tx_queue
        _app.config['STREAM_TX_THREAD'] = _TxThread(
                self._stream_tx_queue, DEST_WEB_STREAM['msg-name'],
                DEST_WEB_STREAM['namespace'], self._inter_msg_time,
                self._error_queue)

        _app.config['RX_QUEUE'] = self._rx_queue
        _app.config['CAMERA_STATUS'] = 'Offline'
        _app.config['CONNECTION_MGR'] = ConnectionMgr()

        print(
                '[INFO] Web interface started at http://localhost:{}'.format(
                        self._port))
        _socketio.run(app=_app, host=self._ipaddr, port=self._port)

    def _check_for_errors(self):
        try:
            error, traceback = self._error_queue.get_nowait()
            print(traceback)
            raise error
        except queue.Empty:
            pass

    def _empty_queue(self, q):
        """Pop items from queue until empty."""
        while (True):
            try:
                q.get_nowait()
            except queue.Empty:
                break

    def close(self):
        """Stop the web server."""
        self._empty_queue(self._content_tx_queue)
        self._empty_queue(self._stream_tx_queue)
        self._empty_queue(self._error_queue)

        # Send the stop command to the Rx thread
        if self._rx_thread.is_alive():
            self._rx_queue.put({'cmd': 'stop'})
            self._rx_thread.join()

    def __enter__(self):
        self.setup()
        return self

    def __exit__(self, type, value, traceback):
        self.close()


@_app.route('/')
def _index():
    """Dashboard home page."""
    hostname = socket.gethostname()
    return render_template(
            'index.html', hostname=hostname)


@_app.route('/video/<video_id>')
def _video(video_id):
    """Video replay page."""
    hostname = socket.gethostname()

    with database.Database() as db:
        result = db.get_for_id(video_id)

    video_filename = os.path.basename(result['path'])
    video_path = os.path.join('recordings', video_filename)

    return render_template(
            'video.html', hostname=hostname,
            date=result['date'], time=result['time'],
            num_people=result['num_people'], video_filename=video_filename,
            video_path=video_path)


def update_camera_status(new_status=None):
    if new_status is not None:
        _app.config['CAMERA_STATUS'] = new_status

    _app.config['CONTENT_TX_QUEUE'].put(
            {'cmd': 'update_status', 'data': _app.config['CAMERA_STATUS']})


@_socketio.on('connect', namespace='/web-content')
def _connect_web_content():
    connection_mgr = _app.config['CONNECTION_MGR']
    if connection_mgr.get_num_connections('/web-content') == 0:
        tx_thread = _app.config['CONTENT_TX_THREAD']
        if not tx_thread.is_alive():
            tx_thread.start()

    connection_mgr.add_connection('/web-content', request.sid)

    update_camera_status()


@_socketio.on('disconnect', namespace='/web-content')
def _disconnect_web_content():
    connection_mgr = _app.config['CONNECTION_MGR']
    connection_mgr.remove_connection('/web-content', request.sid)


@_socketio.on('connect', namespace='/web-stream')
def _connect_web_stream():
    connection_mgr = _app.config['CONNECTION_MGR']
    if connection_mgr.get_num_connections('/web-stream') == 0:
        tx_thread = _app.config['STREAM_TX_THREAD']
        if not tx_thread.is_alive():
            tx_thread.start()

    connection_mgr.add_connection('/web-stream', request.sid)


@_socketio.on('disconnect', namespace='/web-stream')
def _disconnect_web_stream():
    connection_mgr = _app.config['CONNECTION_MGR']
    connection_mgr.remove_connection('/web-stream', request.sid)


@_socketio.on('connect', namespace='/cv')
def _connect_cv():
    connection_mgr = _app.config['CONNECTION_MGR']
    connection_mgr.add_connection('/cv', request.sid)
    update_camera_status('Online')


@_socketio.on('disconnect', namespace='/cv')
def _disconnect_cv():
    connection_mgr = _app.config['CONNECTION_MGR']
    connection_mgr.remove_connection('/web-stream', request.sid)
    update_camera_status('Offline')


@_socketio.on('user-cmd', namespace='/web-content')
def _handle_message_from_user(message):
    _app.config['RX_QUEUE'].put(message)


@_socketio.on('cv-cmd')
def _handle_message_from_cv_app(message):
    connection_mgr = _app.config['CONNECTION_MGR']
    if message['cmd'] in DEST_WEB_CONTENT['msgs']:
        _app.config['CONTENT_TX_QUEUE'].put(message)
    elif message['cmd'] in DEST_WEB_STREAM['msgs']:
        if connection_mgr.get_num_connections('/web-stream') > 0:
            _app.config['STREAM_TX_QUEUE'].put(message)
    elif message['cmd'] in DEST_SERVER:
        _app.config['RX_QUEUE'].put(message)
    else:
        raise AssertionError(
                'Unknown command received by server: {}'.format(
                    message['cmd']))


if __name__ == "__main__":
    web_interface = WebInterface()
    try:
        web_interface.setup()
    finally:
        web_interface.close()
