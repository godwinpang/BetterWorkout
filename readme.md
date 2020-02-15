# Security Camera

A single-camera security system that records events when people are in the frame, and enables video playback for recorded events. The system has three main parts: a computer vision processing app, a socketIO and HTML server app, and a web client.

## Running the App

To run the app using docker-compose, run the following steps from the top level directory:

    $ docker-compose build
    $ docker-compose up

## Computer Vision Processing App

This application is responsible for providing a live video stream and recording interesting events and updating a database with event information. An interesting event is defined as a person or people being in the frame. The source for this application is found in *app.py*, *client.py*, and *database.py*.

The CV processing has three steps:

1. Perform object detection on frame. This produces a list of objects detected in the frame.
1. Filter the predictions by the label 'person'. This produces a list of only the people detected in the frame.
1. Update the centroid tracker with the latest person bounding boxes. The centroid tracker maintains a list of the currently detected people and can be used to determine how many unique people were in the frame during a span of time.

Video state is tracked by the `VideoStateTracker` class. Video state includes:

* Whether an event is active.
* The video file path.
* The date at the start of the event.
* The time at the start of the event.
* The number of people detected throughout the duration of the video.

There can only be one event active at a timei, and videos can span multiple events. Maintaining the state in this way is required due to the asynchrounous nature of `EventVideoWriter`. If another person comes into the frame during post-roll, the same video clip will continue recording, even though a new event has started. When a video clip is saved, an event is set so that in the next iteration of the CV app, the database will be updated with the video state and `VideoStateTracker` will be reset.

### Running the CV App in Standalone Mode
This app has two modes, a standalone mode where it runs independently using the Streamer, and production mode where it connects to the server app. To run the cv app in standalone mode:

    $ cd cv
    $ aai app deploy
    $ aai app start

The Streamer should come up with the live feed and a list of previously recorded videos. As people are detected and new clips are recorded, the panel will be updated.

## Server App

The server app is responsible for hosting the HTML webpage for the device, and communicating with the CV app to provide up-to-date information to the user. The source for the server app is found in *server.py*. The app is a Flask-socketIO app with an Rx thread for handling incoming messages from the CV app and the web client, and a Tx thread for sending messages to the web client. It has a separate namespace for the web clients and CV apps to keep that messaging separated.

## Web Client

The web client is written in javascript and is split into two pages, *templates/index.html* and *templates/video.html*. Both pages show the server hostname and the camera status, and have a link to the live feed page. The index page shows the live video feed when the CV app is running, and lists all the recorded events by month. Clicking one of the videos will bring you to the videos page which shows the information about the video and video playback.

## Messaging

The table below describes all messages between the CV app, Server, and Web client.

Source | Dest | Command | Content | Description
-------|------|---------|---------|------------
CV App | Server | `notify_db_update` | None | Notifies the server that the database has been updated.
CV App | Server | `update_frame` | image in base64 encoding. | A frame from the camera for the live video feed.
Server | Web | `update_text` | Dictionary of dates with a list of videos for each. | Provides data to the web interface for displaying all the recorded videos by date.
Server | Web | `update_frame` | image in base64 encoding. | A frame from the camera for the live video feed.
Server | Web | `update_status` | `Online` or `Offline` | The status of the connection to the CV app.
Server | Web | `notify_db_update` | None | Notifies the server that the database has been updated.
Web Index | Server | `query_db` | None | Requests the `update_text` message from the Server.

### Messaging Sequences

The following table shows the startup messaging sequence, live feed update, and video recording completes messaging sequences.

|CV App | Server | Web Client
|------:|:------:|:----------
|       | Start  |
| Connect to Server | |
|       | Update Camera State: Online |
|       |        | Connect to Server
|       | `update_status(Online)` => |
|       |        | Update camera status
|       |        | <= `query_db`
|       | Query all videos from the database |
|       | `update_text` => |
|       |        | Format the text and display
|       |        |
| ...   | ...    | ...
|       |        |
| Grab frame and perform processing | |
| `update_frame(frame)` => | |
|       | `update_frame(frame)` => |
|       |        | Update video feed image
|       |        |
| ...   | ...    | ...
|       |        |
| Video write completes | |
| Update database | |
| `notify_db_update` => | |
|       | `notify_db_update` => |
|       |        | <= `query_db`
|       | Query all videos from the database |
|       | `update_text` => |
|       |        | Format the text and display
