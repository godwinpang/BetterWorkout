import time
import edgeiq
import os
import cv2
import datetime
import threading
import argparse
import multiprocessing
import multiprocessing.queues
import traceback
import queue
import database
import client


class _CircularQueue(multiprocessing.queues.Queue):
    """Drop oldest item when new item is added to full queue"""
    def __init__(self, *args, **kwargs):
        ctx = multiprocessing.get_context()
        super(_CircularQueue, self).__init__(*args, **kwargs, ctx=ctx)

    def put(self, obj, block=False, timeout=None):
        '''Put an item into the queue.
        If the queue is full remove items until there is room for a new item.
        '''
        if self._closed:
            raise ValueError('Queue {} is closed'.format(self))

        # Semaphore will fail if no spots available. When that happens,
        # pop the oldest item from the queue and add the new one.
        if not self._sem.acquire(block=False, timeout=None):
            with self._rlock:
                # Pop oldest item from queue
                self._recv_bytes()

        with self._notempty:
            if self._thread is None:
                self._start_thread()
            self._buffer.append(obj)
            self._notempty.notify()


class DetectProcess(multiprocessing.Process):
    def __init__(
            self, engine, frame_queue, results_queue, error_queue,
            *args, **kwargs):
        super(DetectProcess, self).__init__(*args, **kwargs)
        self._engine = engine
        self._frame_queue = frame_queue
        self._results_queue = results_queue
        self._error_queue = error_queue

        self._stop_tracking_frames = 15  # 0.5 sec

        self._obj_detect = edgeiq.PoseEstimation(
                "alwaysai/human-pose")
        self._obj_detect.load(engine=self._engine, accelerator=edgeiq.Accelerator.MYRIAD)

        self._loaded_model = self._obj_detect.model_id

        print("Loaded model:\n{}\n".format(self._obj_detect.model_id))
        print("Engine: {}".format(self._obj_detect.engine))
        print("Accelerator: {}\n".format(self._obj_detect.accelerator))
        print("Labels:\n{}\n".format(self._obj_detect.labels))

    # here is where detection is run
    def _run_detection(self):
        while True:
            frame = self._frame_queue.get()
            if isinstance(frame, str) and frame == 'stop':
                break

            results = self._obj_detect.estimate(frame)

            results = {
                "duration": results.duration,
                "model_id": self._loaded_model,
            }

            self._results_queue.put(results)

    def run(self):
        try:
            self._run_detection()
        except Exception as e:
            tb = traceback.format_exc()
            self._error_queue.put((e, tb))


def writer_callback(file_path, event):
    event.set()
    print("Finished writing {}.".format(file_path))


class App:
    def __init__(
            self, cam=0, use_streamer=False, engine=edgeiq.Engine.DNN,
            recording_fps=30, pre_roll_s=1, post_roll_s=1):
        self._cam = cam
        self._use_streamer = use_streamer
        self._engine = engine
        self._recording_fps = recording_fps
        self._pre_roll = pre_roll_s * recording_fps
        self._post_roll = post_roll_s * recording_fps
        self._frame_count_thresh = 30  # 1 sec
        self._prev_results = {
                "duration": 0, "predictions": [], "model_id": "Not loaded",
                "tracked_people": {}}
        self._default_color = (0, 0, 255)
        self._last_frame = None

        self._fps = edgeiq.FPS()
        self._video_complete_event = threading.Event()

        # Queues for interacting with detection process
        self._frame_queue = _CircularQueue(2)
        self._results_queue = multiprocessing.Queue(10)
        self._error_queue = multiprocessing.Queue(1)

        self._video_stream = None
        self._detect_process = None
        self._streamer = None
        self._server_comm = None
        self._video_writer = None

    def setup(self):
        self._video_stream = edgeiq.WebcamVideoStream(cam=self._cam).start()
        # Allow Webcam to warm up
        time.sleep(2.0)

        self._detect_process = DetectProcess(
                self._engine, self._frame_queue, self._results_queue,
                self._error_queue)
        self._detect_process.start()

        if self._use_streamer:
            self._streamer = edgeiq.Streamer()
            self._streamer.setup()
        else:
            self._server_comm = client.ServerComm()
            self._server_comm.setup()

        self._video_writer = edgeiq.EventVideoWriter(
                pre_roll=self._pre_roll,
                post_roll=self._post_roll,
                fps=self._recording_fps,
                codec='H264')

    def _update_detect_process(self, frame):
        self._frame_queue.put(frame)

    def _process_results(self):
        try:
            results = self._results_queue.get_nowait()
        except queue.Empty:
            results = self._prev_results

        # if len(results["tracked_people"]):
        #     if not self._video_tracker.event_active:
        #         self._video_tracker.start()
        #         self._video_writer.start_event(
        #                 output_path=self._video_tracker.file_path,
        #                 callback_function=writer_callback,
        #                 callback_args=(
        #                     self._video_tracker.file_path,
        #                     self._video_complete_event))

        #     self._video_tracker.update(results["tracked_people"])
        # else:
        #     if self._video_tracker.event_active:
        #         self._video_writer.finish_event()
        #         self._video_tracker.stop()

        self._prev_results = results
        return results

    def _print_date_and_time(self, frame):
        current_time_date = str(datetime.datetime.now())
        (h, w) = frame.shape[:2]
        cv2.putText(
                frame, current_time_date, (10, h - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2)

    def _handle_video_complete_event(self):
        if not self._video_complete_event.is_set():
            return
        self._video_complete_event.clear()

        if (not os.path.exists(self._video_tracker.file_path)
                or not os.path.isfile(self._video_tracker.file_path)):
            raise AssertionError('No video file after complete event!'
                    ' {} {}-{}'.format(self._video_tracker.file_path,
                        self._video_tracker.date, self._video_tracker.time))

        with database.Database() as db:
            db.add_entry(
                    self._video_tracker.file_path,
                    self._video_tracker.date,
                    self._video_tracker.time,
                    self._video_tracker.num_people)

        self._video_tracker.reset()

        if not self._use_streamer:
            self._server_comm.send_notify_db_update()

    def _check_for_errors(self):
        try:
            error, traceback = self._error_queue.get_nowait()
            print(traceback)
            raise error
        except queue.Empty:
            pass

    def _check_exit(self):
        """Check if any exit conditions are met."""
        if self._use_streamer:
            return self._streamer.check_exit()
        else:
            return False

    def run(self):
        """Run the CV processing."""
        self._fps.start()

        while True:
            frame = self._video_stream.read()

            # self._update_detect_process(frame)

            # results = self._process_results()

            # self._print_date_and_time(frame)

            # frame = edgeiq.markup_image(
                    # frame, results["predictions"], colors=self._default_color)
            self._video_writer.update(frame)

            web_frame = frame.copy()
            self._server_comm.send_frame(web_frame)

            self._handle_video_complete_event()

            self._fps.update()

            self._check_for_errors()

            if self._check_exit():
                break

    def cleanup(self):
        """Clean up the CV processing."""
        self._fps.stop()
        print("elapsed time: {:.2f}".format(self._fps.get_elapsed_seconds()))
        print("approx. FPS: {:.2f}".format(self._fps.compute_fps()))

        print("Program Ending")

        if self._video_writer:
            self._video_writer.close()

        # Handle any videos completed by the writer close
        self._handle_video_complete_event()

        if self._detect_process:
            self._frame_queue.put('stop')
            self._detect_process.join()

        if self._server_comm:
            self._server_comm.close()

        if self._video_stream:
            self._video_stream.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Security Camera')
    parser.add_argument(
            '--camera', type=int, default=0,
            help='Set the camera index. (Default: 0)')
    parser.add_argument(
            '--use-streamer',  action='store_true',
            help='Use the streamer instead of connecting to the server.')
    parser.add_argument(
            '--engine',  type=str, default='DNN',
            help='The engine to use for CV processing (Default: DNN).')
    parser.add_argument(
            '--recording-fps',  type=int, default=30,
            help='The FPS to record video clips at (Default: 30).')

    args = parser.parse_args()

    engine = edgeiq.Engine.DNN_OPENVINO

    app = App(
            cam=args.camera, use_streamer=args.use_streamer,
            engine=engine, recording_fps=args.recording_fps)
    try:
        app.setup()
        app.run()
    finally:
        app.cleanup()
