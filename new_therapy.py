from multiprocessing import Manager
import logging
from multiprocessing import Process, Queue, Event
import io
from bokeh.io.export import export_png
from bokeh.embed import components
from bokeh.models import HoverTool, ColumnDataSource
from bokeh.plotting import figure, show, output_file
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import cProfile
from scipy.signal import find_peaks
import time
import pyaudio
import librosa
import numpy as np
import cv2
from flask import Flask, redirect, url_for, request, render_template, jsonify, Response
from flask import session, abort

import flask_login
from flask_login import current_user

# AWS Integration
import boto3
from botocore.exceptions import ClientError

# login code
import os
import pathlib
from datetime import datetime

import requests
from flask import Flask, session, abort, redirect, request, render_template
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from pip._vendor import cachecontrol
import google.auth.transport.requests

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore, storage

from flask_mail import Mail, Message

from dotenv import load_dotenv

load_dotenv()

# AWS Rekognition client
rekognition_client = boto3.client(
    'rekognition',
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    region_name=os.environ.get("AWS_REGION", "us-east-1")
)

app = Flask("SonicSerenity")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "default_secret_key")

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.environ.get("MAIL_PASSWORD")

# app.config.update(
#     SESSION_COOKIE_SECURE=True,
#     SESSION_COOKIE_HTTPONLY=True,
#     SESSION_COOKIE_SAMESITE='Lax',
# )

login_manager = flask_login.LoginManager()
login_manager.init_app(app)
# login_manager.login_view = 'login'
# login_manager.session_protection = "strong"

mail = Mail(app)

cred = credentials.Certificate(
    os.environ.get("FIREBASE_CREDENTIALS", "firebase-adminsdk.json"))
firebase_admin.initialize_app(cred, {
    'storageBucket': 'sonicserenity-a3083-3ed0b.firebasestorage.app'
})
db = firestore.client()
bucket = storage.bucket()

doc = db.collection("SIgnal").document("YVNeDHCXVFvg0G3wZc6c")
doc.set({"play": "true"})


os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
client_secrets_file = os.environ.get("GOOGLE_CLIENT_SECRETS_FILE", os.path.join(
    pathlib.Path(__file__).parent, "client_secret.json"))

flow = Flow.from_client_secrets_file(
    client_secrets_file=client_secrets_file,
    scopes=["https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email", "openid"],
    redirect_uri=os.environ.get("OAUTH_REDIRECT_URI", "https://sonic-serenity.com/callback")
    # redirect_uri="http://127.0.0.1:5001/callback"
)


class User(flask_login.UserMixin):
    def __init__(self, id, username, type):
        self.id = id
        self.type = type
        self.username = username

    def get_id(self):
        return self.id


@login_manager.user_loader
def user_loader(email):
    # user = User()
    # user.id = email
    # return user
    if len(user_details):
        return User(user_details['email'], user_details['name'], type[0])
    return None


def login_is_required(function):
    def wrapper(*args, **kwargs):
        if "google_id" not in session:
            return abort(401)  # Authorization required
        else:
            return function(*args, **kwargs)

    # Preserve the original function's metadata
    wrapper.__name__ = function.__name__
    wrapper.__doc__ = function.__doc__
    wrapper.__module__ = function.__module__

    return wrapper



# ---- Per-browser-session state -------------------------------------------
# These used to be plain module-level globals, so every visitor shared one
# login. They are now proxies that resolve to a store keyed by the visitor's
# own session cookie, so existing code (user_details["email"], type[0], ...)
# keeps working unchanged. In-memory: use a single worker process.
import uuid
from collections.abc import MutableMapping, MutableSequence
from flask import has_request_context

_session_stores = {}
_no_request_store = {}


def _store():
    if not has_request_context():
        return _no_request_store
    sid = session.get("sid")
    if sid is None:
        sid = session["sid"] = uuid.uuid4().hex
    return _session_stores.setdefault(sid, {})


class _SessionDict(MutableMapping):
    def __init__(self, name):
        self._name = name

    def _d(self):
        return _store().setdefault(self._name, {})

    def __getitem__(self, k):
        return self._d()[k]

    def __setitem__(self, k, v):
        self._d()[k] = v

    def __delitem__(self, k):
        del self._d()[k]

    def __iter__(self):
        return iter(self._d())

    def __len__(self):
        return len(self._d())

    def __repr__(self):
        return repr(self._d())


class _SessionList(MutableSequence):
    def __init__(self, name, default=()):
        self._name = name
        self._default = list(default)

    def _l(self):
        return _store().setdefault(self._name, list(self._default))

    def __getitem__(self, i):
        return self._l()[i]

    def __setitem__(self, i, v):
        self._l()[i] = v

    def __delitem__(self, i):
        del self._l()[i]

    def __len__(self):
        return len(self._l())

    def insert(self, i, v):
        self._l().insert(i, v)

    def __repr__(self):
        return repr(self._l())


def _drop_session_store():
    sid = session.get("sid")
    if sid:
        _session_stores.pop(sid, None)


steps = [False]
user_session_data = _SessionDict("user_session_data")
therapists_session_data = _SessionList("therapists_session_data")

type = _SessionList("type", [""])


@app.route("/google_login", methods=['POST', 'GET'])
def g_login():
    # if(not steps[0]):
    if request.method == 'POST':
        result = request.form
        a = dict(result)
        type[0] = str(a["type"])

    authorization_url, state = flow.authorization_url()
    session["state"] = state
    return redirect(authorization_url)


user_details = _SessionDict("user_details")
user_data = _SessionDict("user_data")


def check_user(email, type):
    try:
        users_ref = db.collection(type.capitalize())
        print(f"Checking email: {email} in collection: {type.capitalize()}")
        query = users_ref.where('email', '==', email).limit(1)
        results = query.get()
        return len(results) > 0
    except Exception as e:
        print(f'An error occurred: {e}')
        return False

# Function to add a new user if they don't exist


def add_user(user_data, type):
    users_ref = db.collection(type.capitalize())
    users_ref.add(dict(user_data))


@app.route("/callback")
def callback():
    flow.fetch_token(authorization_response=request.url)

    # if not session["state"] == request.args["state"]:
    #     abort(500)  # State does not match!

    credentials = flow.credentials
    request_session = requests.session()
    cached_session = cachecontrol.CacheControl(request_session)
    token_request = google.auth.transport.requests.Request(
        session=cached_session)

    id_info = id_token.verify_oauth2_token(
        id_token=credentials._id_token,
        request=token_request,
        audience=GOOGLE_CLIENT_ID
    )

    print(id_info)
    session["google_id"] = id_info.get("sub")
    session["name"] = id_info.get("name")
    session["profile_image"] = id_info.get("picture")
    user_details["name"] = id_info.get("name").split(" ")[0]
    user_details["full_name"] = id_info.get("name")
    user_details["picture"] = id_info.get("picture")
    user_details["email"] = id_info.get("email")
    user_details["custom_audio"] = {}
    user_data["email"] = id_info.get("email")
    user_data["picture"] = id_info.get("picture")
    user_data["name"] = id_info.get("name").split(" ")[0]
    user_data["full_name"] = id_info.get("name")
    user_data["custom_audio"] = {}

    if (type[0] == ""):
        type[0] = "users"

    user = User(user_data["email"], user_data["name"], type[0])
    flask_login.login_user(user)

    if check_user(user_data["email"], type[0]):
        print("User already exists")
    else:
        add_user(user_data, type[0])
        print("User added successfully")
    return redirect("/protected_area")

# @app.route("/logout")
# def logout():
#     # print(session)
#     # session.clear()
#     # # auth.current_user = None
#     # print(session)
#     # # return redirect("/")
#     return render_template("index.html",logged_in="no", user_details = user_details)


@app.route("/l", methods=["POST", "GET"])
def l():
    _drop_session_store()
    session.clear()
    user_details.clear()
    authenticated_user_type[0] = ""
    return render_template("index.html", flask_login=flask_login, logged_in="no", user_details=user_details)


@app.route("/protected_area")
@login_is_required
def protected_area():
    try:
        if page == "index":
            return render_template("index.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
        elif page == "about":
            return render_template("about_us.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
        elif page == "faq":
            return render_template("faq.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
        elif page == "services":
            return render_template("our_services.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
        elif page == "graph":
            return render_template("graph.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
        elif page == "contact":
            return render_template("contact_us.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
        elif page == "approach":
            return render_template("approach.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
        elif page == "therapists":
            get_auth_type()
            get_session_data()
            print("ghf", authenticated_user_type[0])
            return render_template("therapist_tab.html", flask_login=flask_login, logged_in="yes", user_details=user_details, authenticated_user_type=authenticated_user_type[0], therapists_session_data=therapists_session_data, user_session_data=user_session_data, length=0, alert_msg="")
        else:
            custom_audio = get_custom_audio(user_details["email"])
            custom_audio_url = get_custom_audio_url(
                user_details["email"], custom_audio)
            return render_template("steps.html", flask_login=flask_login, steps="", logged_in="yes", user_details=user_details, id="", custom_audio=custom_audio, custom_audio_url=custom_audio_url)
    except Exception as e:
        print(e)
        custom_audio = get_custom_audio(user_details["email"])
        custom_audio_url = get_custom_audio_url(
            user_details["email"], custom_audio)
        return render_template("steps.html", flask_login=flask_login, steps="", logged_in="yes", user_details=user_details, id="", custom_audio=custom_audio, custom_audio_url=custom_audio_url)


authenticated_user_type = _SessionList("authenticated_user_type", [""])


def check_if_user_logged_in():
    if len(user_details) and current_user.is_authenticated:
        users_ref = db.collection("Users")
        query = users_ref.where('email', '==', current_user.id)
        results = query.get()
        if (len(results)):
            for i in results:
                user_details.update(i.to_dict())
                print(user_details)
                authenticated_user_type[0] = "Users"
        else:
            users_ref = db.collection("Therapists")
            query = users_ref.where('email', '==', current_user.id)
            results = query.get()
            if (len(results)):
                for i in results:
                    user_details.update(i.to_dict())
                    print(user_details)
                    authenticated_user_type[0] = "Therapists"
        return True
    else:
        return False


def get_auth_type():
    if len(user_details) and current_user.is_authenticated:
        users_ref = db.collection("Therapists")
        query = users_ref.where('email', '==', current_user.id)
        results = query.get()
        print(f"usern {current_user.type} id {current_user.id}")
        if (len(results) and current_user.type == "therapists"):
            for i in results:
                user_details.update(i.to_dict())
                print(user_details)
                print("theraps")
                authenticated_user_type[0] = "Therapists"
        else:
            users_ref = db.collection("Users")
            query = users_ref.where('email', '==', current_user.id)
            results = query.get()
            if (len(results)):
                for i in results:
                    user_details.update(i.to_dict())
                    print(user_details)
                    authenticated_user_type[0] = "Users"


@app.route('/')
def start_page():
    # if check_if_user_logged_in():
    # print(user_details["full_name"])
    # return render_template("index.html",flask_login = flask_login,logged_in="yes", user_details = user_details)
   # else:
    return render_template("index.html", flask_login=flask_login, logged_in="no", user_details=user_details)


@app.route("/index")
def index():
    if (len(user_details)):
        return render_template("index.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
    return render_template("index.html", flask_login=flask_login, logged_in="no", user_details=user_details)


@app.route("/login", methods=["POST"])
def login_page():
    global page
    page = ""
    if (not steps[0]):
        if request.method == 'POST':
            result = request.form
            a = dict(result)
            page = a["page"]
    return render_template("login.html")

# therapy code


matplotlib.use('Agg')

# Removed local file initialization (abc.txt, current.txt, history.txt, freqs.txt)
# We now store and read all session/runtime state from Firestore and in-memory shared state.

# Multiprocessing globals are initialized lazily to avoid macOS spawn/reloader issues
manager = None
stop_event = None
capture = None
current_session_id = None
shared_metrics = None
last_dominant_emotion_index = None


def init_shared_state():
    """Initialize multiprocessing-managed shared state. Call from __main__."""
    global manager, stop_event, capture, current_session_id, shared_metrics, last_dominant_emotion_index
    from multiprocessing import Manager, Event
    manager = Manager()
    stop_event = Event()
    # Use manager-backed container so changes are visible across processes
    capture = manager.list([False])
    current_session_id = manager.dict()  # {'id': 'dd-mm-YYYY HH:MM:SS'}
    shared_metrics = manager.dict()
    shared_metrics['times'] = manager.list()
    shared_metrics['emotions'] = manager.dict({
        'Anger': manager.list(),
        'Fear': manager.list(),
        'Happy': manager.list(),
        'Neutral': manager.list(),
        'Sadness': manager.list(),
    })
    shared_metrics['freq_times'] = manager.list()
    shared_metrics['frequences'] = manager.list()
    last_dominant_emotion_index = manager.dict({'value': -1})

# mapping AWS Rekognition emotions to our 5-emotion system


def map_aws_emotions_to_local(aws_emotions):
    """
    Maps AWS Rekognition emotions to local emotion labels
    AWS emotions: HAPPY, SAD, ANGRY, CONFUSED, DISGUSTED, SURPRISED, CALM, UNKNOWN, FEAR
    Local emotions: ['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness'] (indices 0-4)
    """
    emotion_mapping = {
        'HAPPY': 2,      # Happy
        'SAD': 4,        # Sadness
        'ANGRY': 0,      # Anger
        'FEAR': 1,       # Fear
        'CONFUSED': 3,   # Neutral
        'DISGUSTED': 0,  # Anger (closest match)
        'SURPRISED': 3,  # Neutral
        'CALM': 3,       # Neutral
        'UNKNOWN': 3     # Neutral (default)
    }

    # Create prediction array [Anger, Fear, Happy, Neutral, Sadness]
    predictions = np.zeros(5)

    if not aws_emotions:
        # No emotions detected, default to neutral
        predictions[3] = 1.0
        return predictions, 3

    # Sum up confidences for mapped emotions
    for emotion_data in aws_emotions:
        emotion_type = emotion_data['Type']
        confidence = emotion_data['Confidence'] / 100.0  # Convert to 0-1 range

        if emotion_type in emotion_mapping:
            mapped_index = emotion_mapping[emotion_type]
            predictions[mapped_index] += confidence

    # Normalize predictions to sum to 1
    total_confidence = np.sum(predictions)
    if total_confidence > 0:
        predictions = predictions / total_confidence
    else:
        # Fallback to neutral if no valid emotions
        predictions[3] = 1.0

    # Get dominant emotion
    dominant_index = np.argmax(predictions)

    return predictions, dominant_index


# mapping the different emotions to different solfeggio frequencies


def emotion_to_frequency(emotion, dominant_frequency=None):
    emotion_frequencies = {
        'anger': [417, 741],
        'fear': [285, 396],
        'happy': [639, 852, 963],
        'neutral': [dominant_frequency, dominant_frequency],
        'sadness': [174, 432]
    }
    return emotion_frequencies.get(emotion.lower())

# calculating how many semitones to shift a frequency to match another frequency


def calculate_pitch_shift_semitones(target_frequency, reference_frequency):
    return 12 * np.log2(target_frequency / reference_frequency)


# emotion -> target solfeggio frequency used to pitch-shift the background track
EMOTION_TARGET_HZ = {
    'Anger': 417,
    'Fear': 285,
    'Happy': 639,
    'Sadness': 174,
}
MAX_SHIFT_SEMITONES = 12

_track_base_freq_cache = {}


def get_track_base_freq(audio_file, user_email=None):
    """Dominant frequency of the track nearest to a solfeggio tone (cached)."""
    key = (user_email, audio_file)
    if key in _track_base_freq_cache:
        return _track_base_freq_cache[key]

    base = None
    try:
        local_path = os.path.join(app.static_folder, 'audio', audio_file)
        if os.path.isfile(local_path):
            y, sr = librosa.load(local_path, sr=None, mono=True, duration=60)
        else:
            blob = bucket.blob(f"{user_email}/custom_audio/{audio_file}")
            y, sr = librosa.load(io.BytesIO(blob.download_as_bytes()),
                                 sr=None, mono=True, duration=60)
        base = base_freq(y, sr)
        base = float(base) if base else None
    except Exception as e:
        logging.warning(f"Could not detect base frequency for {audio_file}: {e}")

    _track_base_freq_cache[key] = base
    return base


def pitch_shift_for_emotion(emotion, base):
    """Return (target_hz, semitones) for the dominant emotion; neutral keeps the track as is."""
    if not base:
        return None, 0.0
    target = EMOTION_TARGET_HZ.get(emotion, base)
    semitones = calculate_pitch_shift_semitones(target, base)
    semitones = float(np.clip(semitones, -MAX_SHIFT_SEMITONES, MAX_SHIFT_SEMITONES))
    return target, semitones


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


def check_camera_availability():
    """Check if camera is available on the system"""
    try:
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            # Test if we can actually read a frame
            ret, frame = cap.read()
            cap.release()
            return ret and frame is not None
        return False
    except Exception as e:
        logging.warning(f"Camera check failed: {e}")
        return False


def webcam_emotion_detection(start_time, frame_skip=3, stability_check=1):
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        logging.warning(
            "Could not open camera for emotion detection - using neutral baseline")
        # Set neutral baseline data
        try:
            current_time = time.time() - start_time
            last_dominant_emotion_index['value'] = 3  # Neutral
            shared_metrics['times'].append(current_time)
            for label, val in [('Anger', 0.0), ('Fear', 0.0), ('Happy', 0.0), ('Neutral', 1.0), ('Sadness', 0.0)]:
                shared_metrics['emotions'][label].append(val)
        except Exception as e:
            logging.error(f"Failed to set neutral baseline: {e}")
        return

    logging.info("Initialized webcam for AWS Rekognition emotion detection.")
    # Signal Firestore that camera has started; remove local begin
    db.collection("SIgnal").document(
        "YVNeDHCXVFvg0G3wZc6c").set({"play": "true"})
    # Wait until audio starts by polling shared flag set from /turn_audio_on
    data = ""
    emotion_labels = ['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness']
    frame_count = 0
    dominant_emotion_index = -1
    timer_start = None
    last_predictions = np.zeros(5)

    # Wait for audio_start flag instead of reading local file
    while not shared_metrics.get('audio_started', False):
        time.sleep(0.05)

    global stop_event
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            logging.warning("Failed to capture frame from webcam.")
            break

        frame_count += 1
        if frame_count % frame_skip != 0:
            continue

        try:
            # Encode frame for AWS Rekognition
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                logging.warning("Failed to encode frame.")
                continue

            image_bytes = buffer.tobytes()

            # Call AWS Rekognition
            response = rekognition_client.detect_faces(
                Image={'Bytes': image_bytes},
                Attributes=['ALL']
            )

            faces = response.get('FaceDetails', [])
            logging.debug(
                f"Frame {frame_count}: AWS detected {len(faces)} faces.")

            if faces:
                # Use the first detected face
                face = faces[0]
                emotions = face.get('Emotions', [])

                # Map AWS emotions to local format
                predictions, current_dominant_index = map_aws_emotions_to_local(
                    emotions)
                last_predictions = predictions

                logging.debug(f"AWS Emotions: {emotions}")
                logging.debug(f"Mapped Predictions: {predictions}")
                logging.info(
                    f"Dominant emotion index: {current_dominant_index} ({emotion_labels[current_dominant_index]})")

                # Stability check logic
                if dominant_emotion_index != current_dominant_index:
                    timer_start = time.time()
                    dominant_emotion_index = current_dominant_index
                elif timer_start is not None and (time.time() - timer_start) >= stability_check:
                    current_time = time.time() - start_time
                    # update shared state instead of files
                    last_dominant_emotion_index['value'] = dominant_emotion_index
                    shared_metrics['times'].append(current_time)
                    labels = ['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness']
                    for idx, label in enumerate(labels):
                        shared_metrics['emotions'][label].append(
                            float(predictions[idx]))
                    timer_start = None

                # Draw bounding box around face
                bbox = face.get('BoundingBox', {})
                if bbox:
                    frame_height, frame_width = frame.shape[:2]
                    left = int(bbox['Left'] * frame_width)
                    top = int(bbox['Top'] * frame_height)
                    width = int(bbox['Width'] * frame_width)
                    height = int(bbox['Height'] * frame_height)

                    cv2.rectangle(frame, (left, top),
                                  (left + width, top + height), (0, 255, 0), 2)
                    text = f"{emotion_labels[current_dominant_index]}: {predictions[current_dominant_index] * 100:.2f}%"
                    cv2.putText(frame, text, (left, top - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
            else:
                # No face detected - use neutral emotion
                predictions = np.zeros(5)
                predictions[3] = 1.0  # Neutral
                current_dominant_index = 3
                last_predictions = predictions

                # Ensure we at least record a neutral baseline in shared state
                try:
                    current_time = time.time() - start_time
                    last_dominant_emotion_index['value'] = 3
                    if len(shared_metrics['times']) == 0:
                        shared_metrics['times'].append(0.0)
                        for label, val in [('Anger', 0.0), ('Fear', 0.0), ('Happy', 0.0), ('Neutral', 1.0), ('Sadness', 0.0)]:
                            shared_metrics['emotions'][label].append(val)
                except Exception as _e:
                    logging.warning(f"Failed to set neutral baseline: {_e}")

                # Add text indicating no face detected
                cv2.putText(frame, "No face detected", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        except ClientError as e:
            logging.error(f"AWS Rekognition error: {e}")
            # Fallback to neutral if AWS fails
            predictions = np.zeros(5)
            predictions[3] = 1.0
            current_dominant_index = 3
            last_predictions = predictions

            cv2.putText(frame, "AWS Error - Using Neutral", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

        except Exception as e:
            logging.error(f"Unexpected error in emotion detection: {e}")
            # Use last known predictions
            predictions = last_predictions
            current_dominant_index = np.argmax(predictions)

        # Yield frame for streaming
        ret, buffer = cv2.imencode('.jpg', frame)
        fram = buffer.tobytes()
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + fram + b'\r\n')

        if cv2.waitKey(1) & 0xFF == ord('q'):
            logging.info("'q' pressed, exiting webcam loop.")
            break

    logging.info("Camera closed.")
    cap.release()
    cv2.destroyAllWindows()


def background_video_playback(start_time, session_id=None, frame_skip=3, stability_check=1):
    """Play background video with camera feed overlay in bottom right corner"""
    video_path = "static/videos/background_video.mp4"
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        logging.error(f"Could not open video file: {video_path}")
        return

    # Initialize camera for overlay
    camera_cap = None
    try:
        camera_cap = cv2.VideoCapture(0)
        if not camera_cap.isOpened():
            logging.warning(
                "Could not open camera for overlay - continuing without camera feed")
            camera_cap = None
        else:
            logging.info("Camera opened successfully for overlay")
    except Exception as e:
        logging.warning(
            f"Camera initialization failed: {e} - continuing without camera overlay")
        camera_cap = None

    logging.info("Started background video playback with camera overlay.")

    # Signal Firestore that video has started
    db.collection("SIgnal").document(
        "YVNeDHCXVFvg0G3wZc6c").set({"play": "true"})

    # Wait until audio starts by polling shared flag
    while not shared_metrics.get('audio_started', False):
        time.sleep(0.05)

    emotion_labels = ['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness']
    frame_count = 0
    dominant_emotion_index = -1
    timer_start = None
    last_predictions = np.zeros(5)

    # Get video properties
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_delay = 1.0 / fps if fps > 0 else 0.033  # Default to ~30fps

    global stop_event
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            # Loop the video if it reaches the end
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        frame_count += 1
        if frame_count % frame_skip != 0:
            continue

        # Capture camera frame for overlay
        camera_frame = None
        if camera_cap is not None:
            camera_ret, camera_frame = camera_cap.read()
            if not camera_ret:
                camera_frame = None

        # Initialize overlay variables
        overlay_height = frame.shape[0] // 4
        overlay_width = frame.shape[1] // 4
        y_start = frame.shape[0] - overlay_height
        y_end = frame.shape[0]
        x_start = frame.shape[1] - overlay_width
        x_end = frame.shape[1]

        try:
            # Use camera frame for emotion detection if available, otherwise use background video
            emotion_frame = camera_frame if camera_frame is not None else frame

            # Encode frame for AWS Rekognition
            ret, buffer = cv2.imencode('.jpg', emotion_frame)
            if not ret:
                logging.warning("Failed to encode frame.")
                continue

            image_bytes = buffer.tobytes()

            # Call AWS Rekognition
            response = rekognition_client.detect_faces(
                Image={'Bytes': image_bytes},
                Attributes=['ALL']
            )

            faces = response.get('FaceDetails', [])
            logging.debug(
                f"Frame {frame_count}: AWS detected {len(faces)} faces.")

            # Process camera frame with face detection if available
            camera_with_detection = None
            if camera_frame is not None:
                camera_with_detection = camera_frame.copy()

                if faces:
                    # Draw bounding box on camera frame
                    face = faces[0]
                    bbox = face.get('BoundingBox', {})
                    if bbox:
                        camera_height, camera_width = camera_frame.shape[:2]
                        left = int(bbox['Left'] * camera_width)
                        top = int(bbox['Top'] * camera_height)
                        width = int(bbox['Width'] * camera_width)
                        height = int(bbox['Height'] * camera_height)

                        # Draw bounding box on camera frame
                        cv2.rectangle(camera_with_detection, (left, top),
                                      (left + width, top + height), (0, 255, 0), 2)

                        # Add emotion text on camera frame
                        emotions = face.get('Emotions', [])
                        predictions, current_dominant_index = map_aws_emotions_to_local(
                            emotions)
                        text = f"{emotion_labels[current_dominant_index]}: {predictions[current_dominant_index] * 100:.1f}%"
                        cv2.putText(camera_with_detection, text, (left, top - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                else:
                    # No face detected on camera
                    cv2.putText(camera_with_detection, "No face detected",
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

            # Create camera overlay in bottom right corner
            if camera_with_detection is not None:
                camera_resized = cv2.resize(
                    camera_with_detection, (overlay_width, overlay_height))

                # Add border around camera overlay
                cv2.rectangle(frame, (x_start-2, y_start-2),
                              (x_end+2, y_end+2), (255, 255, 255), 2)

                # Overlay camera feed with detection
                frame[y_start:y_end, x_start:x_end] = camera_resized
            elif camera_frame is not None:
                # Fallback: show camera without detection if processing failed
                camera_resized = cv2.resize(
                    camera_frame, (overlay_width, overlay_height))
                cv2.rectangle(frame, (x_start-2, y_start-2),
                              (x_end+2, y_end+2), (255, 255, 255), 2)
                frame[y_start:y_end, x_start:x_end] = camera_resized

            # Process emotions for data collection
            if faces:
                # Use the first detected face
                face = faces[0]
                emotions = face.get('Emotions', [])

                # Map AWS emotions to local format
                predictions, current_dominant_index = map_aws_emotions_to_local(
                    emotions)
                last_predictions = predictions

                logging.debug(f"AWS Emotions: {emotions}")
                logging.debug(f"Mapped Predictions: {predictions}")
                logging.info(
                    f"Dominant emotion index: {current_dominant_index} ({emotion_labels[current_dominant_index]})")

                # Stability check logic
                if dominant_emotion_index != current_dominant_index:
                    timer_start = time.time()
                    dominant_emotion_index = current_dominant_index
                elif timer_start is not None and (time.time() - timer_start) >= stability_check:
                    current_time = time.time() - start_time
                    # Update shared state instead of files
                    last_dominant_emotion_index['value'] = dominant_emotion_index
                    shared_metrics['times'].append(current_time)
                    labels = ['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness']
                    for idx, label in enumerate(labels):
                        shared_metrics['emotions'][label].append(
                            float(predictions[idx]))

                    # Save emotion data to Firebase in real-time
                    if session_id and session_id in therapy_sessions:
                        session_data = therapy_sessions[session_id]
                        user_email = session_data['user_email']
                        emotion_dict = {label: float(
                            predictions[idx]) for idx, label in enumerate(labels)}
                        save_emotion_data_to_firebase(
                            session_id, user_email, current_time, emotion_dict)

                    # Add frequency data (placeholder - can be enhanced with actual frequency detection)
                    shared_metrics['freq_times'].append(current_time)
                    # For now, use a placeholder frequency based on dominant emotion
                    emotion_frequencies = {
                        0: 417,  # Anger
                        1: 285,  # Fear
                        2: 639,  # Happy
                        3: 432,  # Neutral
                        4: 174   # Sadness
                    }
                    frequency = emotion_frequencies.get(
                        current_dominant_index, 432)
                    shared_metrics['frequences'].append(frequency)

                    timer_start = None

                # Draw emotion info on the main frame
                text = f"Emotion: {emotion_labels[current_dominant_index]} ({predictions[current_dominant_index] * 100:.1f}%)"

                # Get frame dimensions for proper text positioning
                frame_height, frame_width = frame.shape[:2]

                # Calculate text size to ensure it fits within frame
                font_scale = 0.6
                font_thickness = 2
                (text_width, text_height), baseline = cv2.getTextSize(
                    text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)

                # Position text with margin from edges
                margin = 10
                x_pos = max(margin, min(10, frame_width - text_width - margin))
                y_pos = max(text_height + margin, 30)

                # Ensure text doesn't go out of bounds
                if x_pos + text_width > frame_width:
                    x_pos = frame_width - text_width - margin
                if y_pos > frame_height:
                    y_pos = frame_height - margin

                cv2.putText(frame, text, (x_pos, y_pos), cv2.FONT_HERSHEY_SIMPLEX,
                            font_scale, (0, 255, 0), font_thickness)
            else:
                # No face detected - use neutral emotion
                predictions = np.zeros(5)
                predictions[3] = 1.0  # Neutral
                current_dominant_index = 3
                last_predictions = predictions

                # Ensure we at least record a neutral baseline in shared state
                try:
                    current_time = time.time() - start_time
                    last_dominant_emotion_index['value'] = 3
                    if len(shared_metrics['times']) == 0:
                        shared_metrics['times'].append(0.0)
                        for label, val in [('Anger', 0.0), ('Fear', 0.0), ('Happy', 0.0), ('Neutral', 1.0), ('Sadness', 0.0)]:
                            shared_metrics['emotions'][label].append(val)
                        # Add frequency data for neutral baseline
                        shared_metrics['freq_times'].append(0.0)
                        shared_metrics['frequences'].append(
                            432)  # Neutral frequency
                except Exception as _e:
                    logging.warning(f"Failed to set neutral baseline: {_e}")

                # Add text indicating no face detected
                text = "No face detected"

                # Get frame dimensions for proper text positioning
                frame_height, frame_width = frame.shape[:2]

                # Calculate text size to ensure it fits within frame
                font_scale = 0.6
                font_thickness = 2
                (text_width, text_height), baseline = cv2.getTextSize(
                    text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)

                # Position text with margin from edges
                margin = 10
                x_pos = max(margin, min(10, frame_width - text_width - margin))
                y_pos = max(text_height + margin, 30)

                # Ensure text doesn't go out of bounds
                if x_pos + text_width > frame_width:
                    x_pos = frame_width - text_width - margin
                if y_pos > frame_height:
                    y_pos = frame_height - margin

                cv2.putText(frame, text, (x_pos, y_pos), cv2.FONT_HERSHEY_SIMPLEX,
                            font_scale, (0, 0, 255), font_thickness)

        except ClientError as e:
            logging.error(f"AWS Rekognition error: {e}")
            # Fallback to neutral if AWS fails
            predictions = np.zeros(5)
            predictions[3] = 1.0
            current_dominant_index = 3
            last_predictions = predictions

            # Add AWS error text with proper positioning
            text = "AWS Error - Using Neutral"

            # Get frame dimensions for proper text positioning
            frame_height, frame_width = frame.shape[:2]

            # Calculate text size to ensure it fits within frame
            font_scale = 0.5
            font_thickness = 2
            (text_width, text_height), baseline = cv2.getTextSize(
                text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)

            # Position text with margin from edges
            margin = 10
            x_pos = max(margin, min(10, frame_width - text_width - margin))
            y_pos = max(text_height + margin, 30)

            # Ensure text doesn't go out of bounds
            if x_pos + text_width > frame_width:
                x_pos = frame_width - text_width - margin
            if y_pos > frame_height:
                y_pos = frame_height - margin

            cv2.putText(frame, text, (x_pos, y_pos), cv2.FONT_HERSHEY_SIMPLEX,
                        font_scale, (0, 0, 255), font_thickness)

        except Exception as e:
            logging.error(f"Unexpected error in emotion detection: {e}")
            # Use last known predictions
            predictions = last_predictions
            current_dominant_index = np.argmax(predictions)

        # Yield frame for streaming
        ret, buffer = cv2.imencode('.jpg', frame)
        fram = buffer.tobytes()
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + fram + b'\r\n')

        # Control frame rate
        time.sleep(frame_delay)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            logging.info("'q' pressed, exiting video playback loop.")
            break

    logging.info("Background video playback ended.")
    cap.release()
    if camera_cap is not None:
        camera_cap.release()
    cv2.destroyAllWindows()


def background_video_only(start_time, frame_skip=3):
    """Play background video without camera access (frontend handles emotion detection)"""
    video_path = "static/videos/background_video.mp4"
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        logging.error(f"Could not open video file: {video_path}")
        return

    logging.info(
        "Started background video playback (no camera access needed).")

    # Signal Firestore that video has started
    db.collection("SIgnal").document(
        "YVNeDHCXVFvg0G3wZc6c").set({"play": "true"})

    # Wait until audio starts by polling shared flag
    while not shared_metrics.get('audio_started', False):
        time.sleep(0.05)

    frame_count = 0

    # Get video properties
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_delay = 1.0 / fps if fps > 0 else 0.033  # Default to ~30fps

    global stop_event
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            # Loop the video if it reaches the end
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        frame_count += 1
        if frame_count % frame_skip != 0:
            continue

        # Add text indicating emotion detection is handled by frontend
        text = "Emotion detection via frontend camera"
        frame_height, frame_width = frame.shape[:2]

        # Calculate text size to ensure it fits within frame
        font_scale = 0.6
        font_thickness = 2
        (text_width, text_height), baseline = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)

        # Position text with margin from edges
        margin = 10
        x_pos = max(margin, min(10, frame_width - text_width - margin))
        y_pos = max(text_height + margin, 30)

        # Ensure text doesn't go out of bounds
        if x_pos + text_width > frame_width:
            x_pos = frame_width - text_width - margin
        if y_pos > frame_height:
            y_pos = frame_height - margin

        cv2.putText(frame, text, (x_pos, y_pos), cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale, (0, 255, 0), font_thickness)

        # Yield frame for streaming
        ret, buffer = cv2.imencode('.jpg', frame)
        fram = buffer.tobytes()
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + fram + b'\r\n')

        # Control frame rate
        time.sleep(frame_delay)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            logging.info("'q' pressed, exiting video playback loop.")
            break

    logging.info("Background video playback ended.")
    cap.release()
    cv2.destroyAllWindows()


def base_freq(y, sr):
    nfft = 2000
    freqs = librosa.fft_frequencies(sr=sr, n_fft=nfft)

    # calculate fft
    # stft faster algo, abs to remove neg, nfft should be twice of max freq u wanna detect
    S = np.abs(librosa.stft(y, n_fft=nfft))

    # mean of fft
    mean_S = np.mean(S, 1)  # 60s divided into many chunks, mean of all chunks
    mean_S_trimmed = mean_S[np.where(freqs > 150)]

    # find loval maxima peaks (indexes)
    peaks, t = find_peaks(mean_S, height=1)

    # find frequencies corresponding to the peak indexes
    peak_values = freqs[peaks]

    solfeggio_freqs = [174, 285, 396, 417, 432, 528, 639, 693, 741, 852, 963]

    # find closest solfeggio frequency to the peak frequencies
    diff = np.inf
    base_freq = None
    d = np.zeros([len(peak_values)*len(solfeggio_freqs), 1])
    i = 0

    for peak_value in peak_values:
        # print(peak_value)
        for target_freq in solfeggio_freqs:
            d = abs(peak_value - target_freq)
            if d < diff:
                # print(peak_value)
                diff = d
                base_freq = peak_value

    return base_freq


def base_freq_ps(y, sr, target):
    nfft = 2000
    freqs = librosa.fft_frequencies(sr=sr, n_fft=nfft)

    # calculate fft
    # stft faster algo, abs to remove neg, nfft should be twice of max freq u wanna detect
    S = np.abs(librosa.stft(y, n_fft=nfft))

    # mean of fft
    mean_S = np.mean(S, 1)  # 60s divided into many chunks, mean of all chunks
    mean_S_trimmed = mean_S[np.where(freqs > 150)]

    # find loval maxima peaks (indexes)
    peaks, t = find_peaks(mean_S, height=1)

    # find frequencies corresponding to the peak indexes
    peak_values = freqs[peaks]

    solfeggio_freqs = [174, 285, 396, 417, 432, 528, 639, 693, 741, 852, 963]

    # find closest solfeggio frequency to the peak frequencies
    diff = np.inf
    base_freq = None
    d = np.zeros([len(peak_values)*len(solfeggio_freqs), 1])
    i = 0

    for peak_value in peak_values:
        # print(peak_value)
        d = abs(peak_value - target)
        if d < diff:
            # print(peak_value)
            diff = d
            base_freq = peak_value

    return base_freq

# modifies the sound in chunks (for a smooth transition between chunks)


def targeted_window(chunk, window_size_ratio=0.01):
    chunk_length = len(chunk)  # chunk in sample length
    # size of the window for fading based on the length of the chunk
    window_size = int(chunk_length * window_size_ratio)
    # window size is at least 2(samples), not too small
    window_size = max(window_size, 2)

    # create a hanning window to apply a fade in and fade out effect to the chunk
    fade_window = np.hanning(window_size * 2)
    # split the window in half for the fade in
    fade_in = fade_window[:window_size]
    fade_out = fade_window[window_size:]  # and half for the fade out
    chunk[:window_size] = chunk[:window_size] * \
        fade_in  # apply fade in at the chunk start
    chunk[-window_size:] = chunk[-window_size:] * \
        fade_out  # and fade out at the chunk end
    return chunk


def get_audio_file(id, audio_file):
    # Return a signed URL for streaming/download instead of saving to disk
    blob = bucket.blob(f"{id}/custom_audio/{audio_file}")
    # For security, you may want to set an expiration or use authenticated URLs
    url = blob.generate_signed_url(version="v4", expiration=3600, method="GET")
    return url


# REMOVED: Complex audio processing function - replaced with simplified session management
# The original audio_processing function has been removed to simplify the codebase
# Audio processing is now handled by the simplified 3-step endpoints


# uploading_audio_in_storage removed: no longer uploading text files; data is written to Firestore Sessions


def graph_db(new, id, audio_name, name):
    try:
        users_ref = db.collection("History")
        query = users_ref.where('user_email', '==', id)
        results = query.get()
        if (results):
            for i in results:
                doc_ref = i.reference
                data = i.to_dict()
                sessions = data["sessions"]
                if (len(sessions) == 5):
                    # s = [j for j in sessions[0]][0]
                    # blobs = bucket.list_blobs(prefix=f'{id}/{s}/')
                    # for blob in blobs:
                    #     blob.delete()
                    sessions.pop(0)
                sessions.append({new: audio_name})
                doc_ref.update({"sessions": sessions})
                print("data added")
        else:
            new_data = {"user_email": id, "sessions": [
                {new: audio_name}], "user_name": name}
            users_ref.add(new_data)
            print("data added")
    except:
        users_ref = db.collection("History")
        new_data = {"user_email": id, "sessions": [
            [new, audio_name]], "user_name": name}
        users_ref.add(new_data)


# REMOVED: Complex upload audio processing function - replaced with simplified session management


# REMOVED: Complex upload audio no processing function - replaced with simplified session management


@app.route('/get_started', methods=['POST', 'GET'])
@login_is_required
def get_started():
    """Simplified get started route - just show the 3-step interface"""
    custom_audio = get_custom_audio(user_details["email"])
    return render_template("steps.html",
                           flask_login=flask_login,
                           logged_in="yes",
                           user_details=user_details,
                           custom_audio=custom_audio)


audio = []


def get_custom_audio(id_):
    doc = db.collection("Users").where("email", "==", id_)
    result = doc.get()
    if result:
        for i in result:
            a = i.to_dict()
            return a["custom_audio"]
    else:
        return {}


def get_custom_audio_url(id, custom_audio):
    a = {}
    for i in custom_audio:
        s = f"{id}%2Fcustom_audio%2F{(i).replace(' ', '%20')}"
        image_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{s}?alt=media"
        print(image_url)
        a.update({i: image_url})
    return a


# REMOVED: Complex load route - replaced with simplified session management


# REMOVED: Complex camera route - replaced with simplified session management


a = [1]


# REMOVED: Complex audio route - replaced with simplified session management


# REMOVED: Complex stop audio route - replaced with simplified session management


@app.route('/show_graph', methods=['POST', 'GET'])
def show_graph():
    try:
        # Fetch latest session from Firestore Sessions
        sessions_ref = db.collection('Sessions').document(
            user_details['email']).collection('Records')
        docs = sessions_ref.order_by(
            'finished_at', direction=firestore.Query.DESCENDING).limit(1).get()

        if not docs:
            return render_template('graph.html', flask_login=flask_login, script="", div="", sc="", di="", user_details=user_details, logged="yes", message="No therapy sessions found. Please complete a therapy session first.")

        doc = docs[0].to_dict()
        session_id = docs[0].id

        times = doc.get('times', [])
        emotions = doc.get('emotions', {})
        freq_times = doc.get('freq_times', [])
        frequences = doc.get('frequences', [])

        # If no data in the main session record, try to get from emotion_data subcollection
        if not times or not emotions:
            emotion_data_ref = db.collection('Sessions').document(user_details['email']).collection(
                'Records').document(session_id).collection('emotion_data')
            emotion_docs = emotion_data_ref.order_by('timestamp').get()

            if emotion_docs:
                times = []
                emotions = {'Anger': [], 'Fear': [],
                            'Happy': [], 'Neutral': [], 'Sadness': []}
                for emotion_doc in emotion_docs:
                    data = emotion_doc.to_dict()
                    times.append(data.get('timestamp', 0))
                    emotion_values = data.get('emotions', {})
                    for emotion in emotions.keys():
                        emotions[emotion].append(
                            emotion_values.get(emotion, 0))
            else:
                return render_template('graph.html', flask_login=flask_login, script="", div="", sc="", di="", user_details=user_details, logged="yes", message="No emotion data found for this session.")

        # Check if we have any data to display
        if not times or len(times) == 0:
            return render_template('graph.html', flask_login=flask_login, script="", div="", sc="", di="", user_details=user_details, logged="yes", message="No emotion data available for this session.")

        output_file("plots.html")

        emotion_source = ColumnDataSource(data=dict(
            Time=times,
            Anger=emotions.get('Anger', []),
            Fear=emotions.get('Fear', []),
            Happy=emotions.get('Happy', []),
            Neutral=emotions.get('Neutral', []),
            Sadness=emotions.get('Sadness', []),
        ))

        emotion_plot = figure(width=800, height=400, title="Emotion Percentages Over Time",
                              x_axis_label='Time', y_axis_label='Emotion Percentages(Weights)')
        emotion_hover = HoverTool(
            tooltips=[
                ("Time", "@Time"),
                ("Anger", "@Anger{0.2f}"),
                ("Fear", "@Fear{0.2f}"),
                ("Happy", "@Happy{0.2f}"),
                ("Neutral", "@Neutral{0.2f}"),
                ("Sadness", "@Sadness{0.2f}"),
            ],
            mode='vline'
        )
        emotion_plot.add_tools(emotion_hover)

        colors = ["orange", "red", "green", "blue", "purple"]
        for emotion, color in zip(['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness'], colors):
            emotion_plot.line('Time', emotion, source=emotion_source,
                              color=color, legend_label=emotion)

        frequency_source = ColumnDataSource(data=dict(
            Time=freq_times,
            Frequency=frequences,
        ))

        frequency_plot = figure(width=800, height=400, title="Frequency Over Time",
                                x_axis_label='Time', y_axis_label='Frequency')
        frequency_hover = HoverTool(
            tooltips=[
                ("Time", "@Time"),
                ("Frequency", "@Frequency"),
            ],
            mode='vline'
        )
        frequency_plot.add_tools(frequency_hover)

        frequency_plot.line('Time', 'Frequency', source=frequency_source,
                            color="black", legend_label='Frequency')

        script, div = components(emotion_plot)
        sc, di = components(frequency_plot)

        return render_template('graph.html', flask_login=flask_login, script=script, div=div, sc=sc, di=di, user_details=user_details, logged="yes")
    except Exception as e:
        print(e)
        return render_template('graph.html', flask_login=flask_login, script="", div="", sc="", di="", user_details=user_details, logged="yes")


@app.route('/upload', methods=["POST", "GET"])
def upload_file():
    if len(user_details):
        id = user_details["email"]
        name = user_details["full_name"]
    else:
        id = ""
        name = ""

    if 'audio_file' not in request.files:
        return 'No file part'

    print("sfydshnj")
    print(f"upload {request.form}")
    data = request.form
    print(data.to_dict())
    file = request.files['audio_file']

    if file.filename == '':
        print("No selected file")

    if file:
        # Store file in memory and upload directly to Firebase Storage
        data_bytes = file.read()
        audio.append(str(file.filename))
        audio[0] = file.filename
        print("File uploaded successfully")
        doc_r = db.collection("Users").where("email", "==", id)
        result = doc_r.get()
        if result:
            for i in result:
                doc_ref_ = i.reference
                d = i.to_dict()
                custom_audio = d.get("custom_audio", {})
                if (len(custom_audio) >= 1):
                    blobs = bucket.list_blobs(prefix=f'{id}/custom_audio/')
                    for blob in blobs:
                        blob.delete()
                blob = bucket.blob(f"{id}/custom_audio/{file.filename}")
                blob.upload_from_string(
                    data_bytes, content_type=file.content_type)

    stop_event.clear()
    start_time = time.time()
    global audio_process
    global webcam_process
    if (not data):
        a = "no"
        print("normal")
        # REMOVED: Complex audio processing - replaced with simplified session management
        print("Audio processing removed - using simplified session management")
    else:
        a = "yes"
        print("abnormal")
        # REMOVED: Complex audio processing - replaced with simplified session management
        print("Audio processing removed - using simplified session management")
    audio_process.start()
    webcam_process = Process(
        target=webcam_emotion_detection, args=(start_time,))
    webcam_process.start()
    custom_audio = get_custom_audio(user_details["email"])
    custom_audio_url = get_custom_audio_url(
        user_details["email"], custom_audio)
    return render_template("steps.html", flask_login=flask_login, step="5", logged_in="yes", user_details=user_details, checkbox=a, custom_audio=custom_audio, custom_audio_url=custom_audio_url)


@app.route('/services', methods=["POST", "GET"])
def services():
    if len(user_details):
        return render_template("our_services.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
    else:
        return render_template("our_services.html", flask_login=flask_login, logged_in="no", user_details=user_details)


@app.route('/about_us', methods=["POST", "GET"])
def about_us():
    if len(user_details):
        return render_template("about_us.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
    else:
        return render_template("about_us.html", flask_login=flask_login, logged_in="no", user_details=user_details)


@app.route('/contact_us', methods=["POST", "GET"])
def contact_us():
    if len(user_details):
        return render_template("contact_us.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
    else:
        return render_template("contact_us.html", flask_login=flask_login, logged_in="no", user_details=user_details)


def format_session_date(session_key):
    """Convert session key (email_timestamp) to formatted date"""
    try:
        # Extract timestamp from session key (format: email_timestamp)
        if '_' in session_key:
            timestamp_str = session_key.split('_')[-1]
            timestamp = int(timestamp_str)
            # Convert to readable date format
            from datetime import datetime
            date_obj = datetime.fromtimestamp(timestamp)
            return date_obj.strftime("%B %d, %Y at %I:%M %p")
        else:
            return session_key  # Return as-is if format is unexpected
    except (ValueError, IndexError):
        return session_key  # Return as-is if parsing fails


def get_session_data():
    user_session_data.clear()

    # First try to get sessions from Sessions collection (new format with emotion data)
    try:
        sessions_ref = db.collection('Sessions').document(
            user_details["email"]).collection('Records')
        sessions_docs = sessions_ref.order_by(
            'finished_at', direction=firestore.Query.DESCENDING).limit(5).get()
    except Exception as e:
        print(
            f"Error accessing Sessions collection for {user_details['email']}: {e}")
        sessions_docs = []

    if sessions_docs:
        # New format - get sessions from Sessions collection
        formatted_sessions = []
        for doc in sessions_docs:
            session_data = doc.to_dict()
            session_id = doc.id
            audio_file = session_data.get('audio_file', 'Unknown')

            # Format session date
            formatted_key = format_session_date(session_id)
            formatted_session = {
                formatted_key: {
                    'audio_name': audio_file,
                    'original_key': session_id
                }
            }
            formatted_sessions.append(formatted_session)

        user_session_data['sessions'] = formatted_sessions
        user_session_data['user_name'] = user_details.get(
            'full_name', user_details.get('name', 'User'))
        user_session_data['user_email'] = user_details['email']
    else:
        # Fallback to History collection for old sessions
        users_ref = db.collection("History")
        query = users_ref.where(
            'user_email', '==', user_details["email"]).limit(1)
        results = query.get()
        if (results):
            for i in results:
                data = i.to_dict()
                # Format session dates while preserving original keys for forms
                if 'sessions' in data:
                    formatted_sessions = []
                    for session in data['sessions']:
                        formatted_session = {}
                        for key, value in session.items():
                            formatted_key = format_session_date(key)
                            # Store both formatted date and original key
                            formatted_session[formatted_key] = {
                                'audio_name': value,
                                'original_key': key
                            }
                        formatted_sessions.append(formatted_session)
                    data['sessions'] = formatted_sessions
                user_session_data.update(data)
                # Ensure user_email is set for sharing functionality
                if 'user_email' not in user_session_data:
                    user_session_data['user_email'] = user_details['email']

    therapists_session_data.clear()
    therapists_ref = db.collection("Shared Sessions")
    t_query = therapists_ref.where(
        'therapist_email', '==', user_details["email"])
    t_results = t_query.get()
    print(t_results)
    if (t_results):
        for i in t_results:
            therapists_session_data.append(i.to_dict())


@app.template_filter('enumerate')
def do_enumerate(iterable):
    return enumerate(iterable)


@app.route('/therapists', methods=["POST", "GET"])
def therapists():
    if len(user_details) and current_user.is_authenticated:
        get_auth_type()
        get_session_data()
        print(f"user_session data {user_session_data}")
        print(f"therpists {therapists_session_data}")
        print(f"auth{authenticated_user_type[0]}")
        l = 0
        if user_session_data:
            if (len(user_session_data["sessions"])):
                l = len(user_session_data["sessions"])
            else:
                l = 0
        return render_template("therapist_tab.html", flask_login=flask_login, logged_in="yes", user_details=user_details, authenticated_user_type=authenticated_user_type[0], therapists_session_data=therapists_session_data, user_session_data=user_session_data, length_=l, alert_msg="")
    else:
        return render_template("therapist_tab.html", flask_login=flask_login, logged_in="no", user_details=user_details, authenticated_user_type=authenticated_user_type[0], therapists_session_data=therapists_session_data, user_session_data=user_session_data, length_=0, alert_msg="")


@app.route("/show_patient_sessions", methods=["POST", "GET"])
def show_patient_sessions():
    if request.method == 'POST':
        b = request.form
        patient_email = b["p_email"]

        # Validate patient_email to prevent trailing slash error
        if not patient_email or patient_email.strip() == "":
            return render_template("patient_data.html", flask_login=flask_login, user_details=user_details, patient_data={}, error="Invalid patient email provided.")

        # Remove any trailing slashes or whitespace
        patient_email = patient_email.strip().rstrip('/')

        # Debug print to help identify the issue
        print(
            f"DEBUG: Processing patient email: '{patient_email}' (length: {len(patient_email)})")

        patient_data = {}

        # First try to get sessions from Sessions collection (new format with emotion data)
        try:
            sessions_ref = db.collection('Sessions').document(
                patient_email).collection('Records')
            sessions_docs = sessions_ref.order_by(
                'finished_at', direction=firestore.Query.DESCENDING).limit(5).get()
        except Exception as e:
            print(
                f"Error accessing Sessions collection for {patient_email}: {e}")
            sessions_docs = []

        if sessions_docs:
            # New format - get sessions from Sessions collection
            formatted_sessions = []
            for doc in sessions_docs:
                session_data = doc.to_dict()
                session_id = doc.id
                audio_file = session_data.get('audio_file', 'Unknown')

                # Format session date
                formatted_key = format_session_date(session_id)
                formatted_session = {
                    formatted_key: {
                        'audio_name': audio_file,
                        'original_key': session_id
                    }
                }
                formatted_sessions.append(formatted_session)

            patient_data['sessions'] = formatted_sessions
            patient_data['patient_name'] = session_data.get(
                'user_name', 'Patient')
            patient_data['patient_email'] = patient_email
        else:
            # Fallback to Shared Sessions collection for old sessions
            doc = db.collection("Shared Sessions").where(
                "patient_email", "==", patient_email)
            result = doc.get()
            if result:
                for i in result:
                    data = i.to_dict()
                    # Format session dates for patient data while preserving original keys
                    if 'sessions' in data:
                        formatted_sessions = []
                        for session in data['sessions']:
                            formatted_session = {}
                            for key, value in session.items():
                                formatted_key = format_session_date(key)
                                # Store both formatted date and original key
                                formatted_session[formatted_key] = {
                                    'audio_name': value,
                                    'original_key': key
                                }
                            formatted_sessions.append(formatted_session)
                        data['sessions'] = formatted_sessions
                    patient_data.update(data)

        return render_template("patient_data.html", flask_login=flask_login, user_details=user_details, patient_data=patient_data)


@app.route('/share_graphs', methods=["POST", "GET"])
def share_graphs():
    if request.method == 'POST':
        flag = False
        result = request.form
        a = dict(result)
        sessions = a["sessions"]
        sessions = sessions.split(",")
        sessions.pop()
        print(f"DEBUG: share_graphs - Raw sessions data: {a}")
        print(f"DEBUG: share_graphs - Processed sessions: {sessions}")
        for i in range(len(sessions)):
            sessions[i] = {sessions[i].split(
                "+")[0]: sessions[i].split("+")[1]}
        print(f"DEBUG: share_graphs - Final sessions dict: {sessions}")
        therapist_email = a["therapist_email"]
        doc = db.collection("Shared Sessions").where("patient_email", "==", a["user_email"]).where(
            "therapist_email", "==", a["therapist_email"])
        result = doc.get()
        if (len(result)):
            for i in result:
                result_doc = i.reference
                result_data = i.to_dict()
                existing_sessions = result_data["sessions"]
                for i in sessions:
                    if i in existing_sessions:
                        continue
                    else:
                        if (len(existing_sessions) == 10):
                            existing_sessions.pop(0)
                            blobs = bucket.list_blobs(
                                prefix=f'{a["user_email"]}/{existing_sessions[0]}/')
                            for blob in blobs:
                                blob.delete()
                        existing_sessions.append(i)
                result_doc.update({"sessions": existing_sessions})
        else:
            doc = db.collection("Shared Sessions")
            data = {"patient_name": a["user_name"], "patient_email": a["user_email"],
                    "sessions": sessions, "therapist_email": a["therapist_email"]}
            doc.add(data)
        recipient = therapist_email
        subject = "New Session Shared"
        body = f"{a['user_name'].capitalize()} shared session with you. Login to SonicSerenity to check out."
        msg = Message(
            subject, sender=app.config['MAIL_USERNAME'], recipients=[recipient])
        msg.body = body

        try:
            mail.send(msg)
            print('Email sent successfully!')
            return render_template("therapist_tab.html", flask_login=flask_login, logged_in="yes", user_details=user_details, authenticated_user_type=authenticated_user_type[0], therapists_session_data=therapists_session_data, user_session_data=user_session_data, length_=len(user_session_data["sessions"]), alert_msg="success")
        except Exception as e:
            print(f'Failed to send email: {str(e)}')
            return render_template("therapist_tab.html", flask_login=flask_login, logged_in="yes", user_details=user_details, authenticated_user_type=authenticated_user_type[0], therapists_session_data=therapists_session_data, user_session_data=user_session_data, length_=0, alert_msg="unsuccess")


@app.route('/see_graphs_t', methods=["POST", "GET"])
def see_graphs_t():
    if request.method == 'POST':
        result = request.form
        a = dict(result)
        session_name = a["session"]
        id = a["email"]
        name = a["name"]

        # Validate inputs to prevent trailing slash error
        if not id or id.strip() == "" or not session_name or session_name.strip() == "":
            return render_template('see_graph_therapist.html', script="", div="", sc="", di="", user_details=user_details, logged="yes", patient_name=name, message="Invalid session or email provided.")

        # Remove any trailing slashes or whitespace
        id = id.strip().rstrip('/')
        session_name = session_name.strip().rstrip('/')

        # Debug print to help identify the issue
        print(
            f"DEBUG: Processing see_graphs_t - id: '{id}', session_name: '{session_name}'")

        try:
            # First try to fetch from Sessions collection (new format)
            sessions_ref = db.collection('Sessions').document(
                id).collection('Records').document(session_name)
            doc = sessions_ref.get()

            times = []
            emotions = {}
            freq_times = []
            frequences = []

            if doc.exists:
                # Data is in Sessions collection
                payload = doc.to_dict()
                times = payload.get('times', [])
                emotions = payload.get('emotions', {})
                freq_times = payload.get('freq_times', [])
                frequences = payload.get('frequences', [])
            else:
                # Session not found in Sessions collection
                return render_template('see_graph_therapist.html', script="", div="", sc="", di="", user_details=user_details, logged="yes", patient_name=name, message="Session not found. This session may not have been completed properly.")

            # Check if we have any data to display
            if not times or not emotions or len(times) == 0:
                return render_template('see_graph_therapist.html', script="", div="", sc="", di="", user_details=user_details, logged="yes", patient_name=name, message="No emotion data available for this session.")

            output_file("plots.html")

            emotion_source = ColumnDataSource(data=dict(
                Time=times,
                Anger=emotions.get('Anger', []),
                Fear=emotions.get('Fear', []),
                Happy=emotions.get('Happy', []),
                Neutral=emotions.get('Neutral', []),
                Sadness=emotions.get('Sadness', []),
            ))
            # If no data in the main session record, try to get from emotion_data subcollection
            if not times or not emotions:
                emotion_data_ref = db.collection('Sessions').document(id).collection(
                    'Records').document(session_name).collection('emotion_data')
                emotion_docs = emotion_data_ref.order_by('timestamp').get()

                if emotion_docs:
                    times = []
                    emotions = {'Anger': [], 'Fear': [],
                                'Happy': [], 'Neutral': [], 'Sadness': []}
                    for emotion_doc in emotion_docs:
                        data = emotion_doc.to_dict()
                        times.append(data.get('timestamp', 0))
                        emotion_values = data.get('emotions', {})
                        for emotion in emotions.keys():
                            emotions[emotion].append(
                                emotion_values.get(emotion, 0))

            # Build DataFrames directly from Firestore payload (no local files)
            weights = pd.DataFrame({
                'Time': times,
                'Anger': emotions.get('Anger', []),
                'Fear': emotions.get('Fear', []),
                'Happy': emotions.get('Happy', []),
                'Neutral': emotions.get('Neutral', []),
                'Sadness': emotions.get('Sadness', []),
            })
            freq_df = pd.DataFrame(
                {'Time': freq_times, 'Frequency': frequences})

            output_file("plots.html")

            emotion_source = ColumnDataSource(data=dict(
                Time=weights['Time'],
                Anger=weights['Anger'],
                Fear=weights['Fear'],
                Happy=weights['Happy'],
                Neutral=weights['Neutral'],
                Sadness=weights['Sadness'],
            ))

            emotion_plot = figure(width=800, height=400, title="Emotion Percentages Over Time",
                                  x_axis_label='Time', y_axis_label='Emotion Percentages(Weights)')
            emotion_hover = HoverTool(
                tooltips=[
                    ("Time", "@Time"),
                    ("Anger", "@Anger{0.2f}"),
                    ("Fear", "@Fear{0.2f}"),
                    ("Happy", "@Happy{0.2f}"),
                    ("Neutral", "@Neutral{0.2f}"),
                    ("Sadness", "@Sadness{0.2f}"),
                ],
                mode='vline'
            )
            emotion_plot.add_tools(emotion_hover)

            colors = ["orange", "red", "green", "blue", "purple"]
            for emotion, color in zip(['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness'], colors):
                emotion_plot.line(
                    'Time', emotion, source=emotion_source, color=color, legend_label=emotion)

            frequency_source = ColumnDataSource(data=dict(
                Time=freq_df['Time'],
                Frequency=freq_df['Frequency'],
            ))

            frequency_plot = figure(width=800, height=400, title="Frequency Over Time",
                                    x_axis_label='Time', y_axis_label='Frequency')
            frequency_hover = HoverTool(
                tooltips=[
                    ("Time", "@Time"),
                    ("Frequency", "@Frequency"),
                ],
                mode='vline'
            )

            frequency_plot.add_tools(frequency_hover)

            frequency_plot.line('Time', 'Frequency', source=frequency_source,
                                color="black", legend_label='Frequency')

            script, div = components(emotion_plot)
            sc, di = components(frequency_plot)

            # Plotting Emotion Percentages Over Time
            fig, (ax1, ax2) = plt.subplots(nrows=2, ncols=1, figsize=(10, 8))

            # Plot Emotion Percentages
            for emotion, color in zip(['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness'], ["orange", "red", "green", "blue", "purple"]):
                ax1.plot(weights['Time'], weights[emotion],
                         color=color, label=emotion)

            ax1.set_title("Emotion Percentages Over Time")
            ax1.set_xlabel('Time')
            ax1.set_ylabel('Emotion Percentages(Weights)')
            ax1.legend()

            # Plot Frequency
            ax2.plot(freq_df['Time'], freq_df['Frequency'],
                     color="black", label='Frequency')
            ax2.set_title("Frequency Over Time")
            ax2.set_xlabel('Time')
            ax2.set_ylabel('Frequency')
            ax2.legend()

            # Super title for the combined plot
            fig.suptitle(
                f"{name.title()}'s {session_name} Session", fontsize=14)
            print(session_name)
            plt.tight_layout()
            plt.savefig('static/images/plots.png')
            plt.close()

            # blob = bucket.blob("Graph Image/plots.png")
            # blob.upload_from_filename("show_graph/plots.png")
            # image_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/Graph%20Image%2Fplots.png?alt=media"

            # # Print the URL for testing
            # print(f"Download URL: {image_url}")

            return render_template('see_graph_therapist.html', script=script, div=div, sc=sc, di=di, user_details=user_details, logged="yes", patient_name=name)
        except Exception as e:
            print(e)
            return render_template('see_graph_therapist.html', script="", div="", sc="", di="", user_details=user_details, logged="yes", patient_name=name)


@app.route('/see_graphs', methods=["POST", "GET"])
def see_graphs():
    if request.method == 'POST':
        result = request.form
        a = dict(result)
        session_name = a["session"]
        id = user_details["email"]

        # Validate inputs to prevent trailing slash error
        if not id or id.strip() == "" or not session_name or session_name.strip() == "":
            return render_template('see_graph.html', flask_login=flask_login, script="", div="", sc="", di="", user_details=user_details, logged="yes", message="Invalid session or email provided.")

        # Remove any trailing slashes or whitespace
        id = id.strip().rstrip('/')
        session_name = session_name.strip().rstrip('/')

        try:
            # First try to fetch from Sessions collection (new format)
            sessions_ref = db.collection('Sessions').document(
                id).collection('Records').document(session_name)
            doc = sessions_ref.get()

            times = []
            emotions = {}
            freq_times = []
            frequences = []

            if doc.exists:
                # Data is in Sessions collection
                payload = doc.to_dict()
                times = payload.get('times', [])
                emotions = payload.get('emotions', {})
                freq_times = payload.get('freq_times', [])
                frequences = payload.get('frequences', [])

                # If no data in the main session record, try to get from emotion_data subcollection
                if not times or not emotions:
                    emotion_data_ref = db.collection('Sessions').document(id).collection(
                        'Records').document(session_name).collection('emotion_data')
                    emotion_docs = emotion_data_ref.order_by('timestamp').get()

                    if emotion_docs:
                        times = []
                        emotions = {'Anger': [], 'Fear': [],
                                    'Happy': [], 'Neutral': [], 'Sadness': []}
                        for emotion_doc in emotion_docs:
                            data = emotion_doc.to_dict()
                            times.append(data.get('timestamp', 0))
                            emotion_values = data.get('emotions', {})
                            for emotion in emotions.keys():
                                emotions[emotion].append(
                                    emotion_values.get(emotion, 0))
            else:
                # Session not found in Sessions collection
                return render_template('see_graph.html', flask_login=flask_login, script="", div="", sc="", di="", user_details=user_details, logged="yes", message="Session not found. This session may not have been completed properly.")

            # Check if we have any data to display
            if not times or not emotions or len(times) == 0:
                return render_template('see_graph.html', flask_login=flask_login, script="", div="", sc="", di="", user_details=user_details, logged="yes", message="No emotion data available for this session.")

            output_file("plots.html")

            emotion_source = ColumnDataSource(data=dict(
                Time=times,
                Anger=emotions.get('Anger', []),
                Fear=emotions.get('Fear', []),
                Happy=emotions.get('Happy', []),
                Neutral=emotions.get('Neutral', []),
                Sadness=emotions.get('Sadness', []),
            ))

            emotion_plot = figure(width=800, height=400, title="Emotion Percentages Over Time",
                                  x_axis_label='Time', y_axis_label='Emotion Percentages(Weights)')
            emotion_hover = HoverTool(
                tooltips=[
                    ("Time", "@Time"),
                    ("Anger", "@Anger{0.2f}"),
                    ("Fear", "@Fear{0.2f}"),
                    ("Happy", "@Happy{0.2f}"),
                    ("Neutral", "@Neutral{0.2f}"),
                    ("Sadness", "@Sadness{0.2f}"),
                ],
                mode='vline'
            )
            emotion_plot.add_tools(emotion_hover)

            colors = ["orange", "red", "green", "blue", "purple"]
            for emotion, color in zip(['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness'], colors):
                emotion_plot.line(
                    'Time', emotion, source=emotion_source, color=color, legend_label=emotion)

            frequency_source = ColumnDataSource(data=dict(
                Time=freq_times,
                Frequency=frequences,
            ))

            frequency_plot = figure(width=800, height=400, title="Frequency Over Time",
                                    x_axis_label='Time', y_axis_label='Frequency')
            frequency_hover = HoverTool(
                tooltips=[
                    ("Time", "@Time"),
                    ("Frequency", "@Frequency"),
                ],
                mode='vline'
            )

            frequency_plot.add_tools(frequency_hover)

            frequency_plot.line('Time', 'Frequency', source=frequency_source,
                                color="black", legend_label='Frequency')

            script, div = components(emotion_plot)
            sc, di = components(frequency_plot)

            return render_template('see_graph.html', flask_login=flask_login, script=script, div=div, sc=sc, di=di, user_details=user_details, logged="yes")
        except Exception as e:
            print(f"Error in see_graphs: {e}")
            return render_template('see_graph.html', flask_login=flask_login, script="", div="", sc="", di="", user_details=user_details, logged="yes", message="Error loading session data.")


@app.route('/faq', methods=["POST", "GET"])
def faq():
    if len(user_details):
        return render_template("faq.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
    else:
        return render_template("faq.html", flask_login=flask_login, logged_in="no", user_details=user_details)


# Removed video_feed route - now using frontend camera display

@app.route('/static/audio/<filename>')
def serve_audio(filename):
    """Serve audio files from static/audio directory"""
    try:
        return app.send_static_file(f'audio/{filename}')
    except Exception as e:
        print(f"Audio file not found: {filename}")
        return "Audio file not found", 404


@app.route('/static/videos/<filename>')
def serve_video(filename):
    """Serve video files from static/videos directory"""
    try:
        return app.send_static_file(f'videos/{filename}')
    except Exception as e:
        print(f"Video file not found: {filename}")
        return "Video file not found", 404


@app.route('/upload_emotion_data', methods=['POST'])
@login_is_required
def upload_emotion_data():
    """Receive emotion data from client-side camera"""
    try:
        data = request.get_json()
        emotions = data.get('emotions', {})
        timestamp = data.get('timestamp', time.time())

        session_id, session_data = _active_session()
        if session_data:
            user_email = session_data['user_email']

            # Save emotion data to Firebase
            save_emotion_data_to_firebase(
                session_id, user_email, timestamp, emotions)

            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'No active session'})

    except Exception as e:
        print(f"Error uploading emotion data: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/process_frame', methods=['POST'])
@login_is_required
def process_frame():
    """Process frame sent from frontend for emotion detection"""
    try:
        if 'frame' not in request.files:
            return jsonify({'success': False, 'error': 'No frame provided'})

        frame_file = request.files['frame']
        timestamp = float(request.form.get('timestamp', time.time()))

        session_id, session_data = _active_session()
        if not session_data:
            return jsonify({'success': False, 'error': 'No active session'})

        # Read frame data
        frame_data = frame_file.read()

        # Convert to numpy array for OpenCV processing
        nparr = np.frombuffer(frame_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({'success': False, 'error': 'Invalid frame data'})

        # Process frame with AWS Rekognition
        try:
            # Encode frame for AWS Rekognition
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                return jsonify({'success': False, 'error': 'Failed to encode frame'})

            image_bytes = buffer.tobytes()

            # Call AWS Rekognition
            response = rekognition_client.detect_faces(
                Image={'Bytes': image_bytes},
                Attributes=['ALL']
            )

            faces = response.get('FaceDetails', [])

            if faces:
                # Use the first detected face
                face = faces[0]
                emotions = face.get('Emotions', [])

                # Map AWS emotions to local format
                predictions, current_dominant_index = map_aws_emotions_to_local(
                    emotions)

                # Create emotion dictionary
                emotion_labels = ['Anger', 'Fear',
                                  'Happy', 'Neutral', 'Sadness']
                emotion_dict = {label: float(
                    predictions[idx]) for idx, label in enumerate(emotion_labels)}

                # Save to this session's own metrics and Firebase
                metrics = session_data['metrics']
                current_time = time.time() - session_data.get('therapy_start_time', time.time())
                metrics['times'].append(current_time)
                for idx, label in enumerate(emotion_labels):
                    metrics['emotions'][label].append(
                        float(predictions[idx]))

                # Add frequency data
                emotion_frequencies = {
                    0: 417,  # Anger
                    1: 285,  # Fear
                    2: 639,  # Happy
                    3: 432,  # Neutral
                    4: 174   # Sadness
                }
                frequency = emotion_frequencies.get(
                    current_dominant_index, 432)
                metrics['freq_times'].append(current_time)
                metrics['frequences'].append(frequency)

                # Save to Firebase
                user_email = session_data['user_email']
                save_emotion_data_to_firebase(
                    session_id, user_email, timestamp, emotion_dict)

                dominant = emotion_labels[current_dominant_index]
                target_hz, semitones = pitch_shift_for_emotion(
                    dominant, session_data.get('base_freq'))
                return jsonify({
                    'success': True,
                    'emotions': emotion_dict,
                    'dominant_emotion': dominant,
                    'confidence': float(predictions[current_dominant_index]),
                    'target_frequency': target_hz,
                    'semitones': semitones
                })
            else:
                # No face detected - use neutral emotion
                emotion_dict = {
                    'Anger': 0.0,
                    'Fear': 0.0,
                    'Happy': 0.0,
                    'Neutral': 1.0,
                    'Sadness': 0.0
                }

                # Save neutral baseline
                user_email = session_data['user_email']
                save_emotion_data_to_firebase(
                    session_id, user_email, timestamp, emotion_dict)

                return jsonify({
                    'success': True,
                    'emotions': emotion_dict,
                    'dominant_emotion': 'Neutral',
                    'confidence': 1.0,
                    'semitones': 0.0
                })

        except ClientError as e:
            logging.error(f"AWS Rekognition error: {e}")
            # Fallback to neutral
            emotion_dict = {
                'Anger': 0.0,
                'Fear': 0.0,
                'Happy': 0.0,
                'Neutral': 1.0,
                'Sadness': 0.0
            }
            return jsonify({
                'success': True,
                'emotions': emotion_dict,
                'dominant_emotion': 'Neutral',
                'confidence': 1.0,
                'semitones': 0.0
            })

    except Exception as e:
        logging.error(f"Error processing frame: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/approach', methods=["POST", "GET"])
def approach():
    if len(user_details):
        return render_template("approach.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
    else:
        return render_template("approach.html", flask_login=flask_login, logged_in="no", user_details=user_details)


@app.route('/sonic_sounds', methods=["POST", "GET"])
def sonic_sounds():
    if len(user_details):
        return render_template("sonic_sounds.html", flask_login=flask_login, logged_in="yes", user_details=user_details)
    else:
        return render_template("sonic_sounds.html", flask_login=flask_login, logged_in="no", user_details=user_details)


@app.route('/client_emotion_detection')
@login_is_required
def client_emotion_detection():
    """Client-side emotion detection page"""
    return render_template("client_emotion_detection.html")


# Simplified 3-Step Therapy Endpoints
# Global session storage (in-memory, simplified), keyed by session id.
# Each browser remembers its own session id in its cookie, so concurrent
# listeners never share a session.
therapy_sessions = {}


def _active_session():
    """The calling browser's own therapy session, as (id, data) or (None, None)."""
    session_id = session.get('therapy_session_id')
    return session_id, therapy_sessions.get(session_id)

# Function to save emotion data to Firebase in real-time


def save_emotion_data_to_firebase(session_id, user_email, timestamp, emotions, frequency=None):
    """Save emotion data to Firebase in real-time during session"""
    try:
        # Create emotion data document
        emotion_data = {
            'timestamp': timestamp,
            'emotions': emotions,
            'created_at': firestore.SERVER_TIMESTAMP
        }

        if frequency is not None:
            emotion_data['frequency'] = frequency

        # Save to Firebase under the session's emotion_data subcollection
        emotion_ref = db.collection('Sessions').document(user_email).collection(
            'Records').document(session_id).collection('emotion_data').document()
        emotion_ref.set(emotion_data)

        return True
    except Exception as e:
        print(f"Error saving emotion data to Firebase: {e}")
        return False


@app.route('/start_session', methods=['POST'])
@login_is_required
def start_session():
    """Step 1: Initialize therapy session with selected audio"""
    try:
        data = request.get_json()
        audio_file = data.get('audio', 'tibetan.wav')

        if not user_details:
            return jsonify({'success': False, 'error': 'User not authenticated'})

        base = get_track_base_freq(audio_file, user_details['email'])

        # Create session ID
        session_id = f"{user_details['email']}_{int(time.time())}"

        # Initialize session data
        therapy_sessions[session_id] = {
            'user_email': user_details['email'],
            'user_name': user_details.get('full_name', user_details.get('name', 'User')),
            'audio_file': audio_file,
            'start_time': time.time(),
            'emotions': [],
            'frequencies': [],
            'base_freq': base,
            'status': 'initialized',
            'metrics': {
                'times': [],
                'emotions': {e: [] for e in ['Anger', 'Fear', 'Happy', 'Neutral', 'Sadness']},
                'freq_times': [],
                'frequences': [],
            }
        }

        session['therapy_session_id'] = session_id

        return jsonify({'success': True, 'session_id': session_id, 'base_freq': base})

    except Exception as e:
        print(f"Error starting session: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/begin_therapy', methods=['POST'])
@login_is_required
def begin_therapy():
    """Step 2: Start the actual therapy session"""
    try:
        _, session_data = _active_session()
        if not session_data:
            return jsonify({'success': False, 'error': 'No active session'})

        session_data['status'] = 'active'
        session_data['therapy_start_time'] = time.time()

        # Initialize emotion detection (handled by frontend frame capture)
        session_data['emotion_detection_active'] = True

        return jsonify({'success': True, 'message': 'Therapy session started'})

    except Exception as e:
        print(f"Error beginning therapy: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/end_session', methods=['POST'])
@login_is_required
def end_session():
    """Step 3: End therapy session and save data"""
    try:
        session_id, session_data = _active_session()
        if not session_data:
            return jsonify({'success': False, 'error': 'No active session'})

        session_data['status'] = 'completed'
        session_data['end_time'] = time.time()
        session_data['duration'] = session_data['end_time'] - \
            session_data['therapy_start_time']

        # Stop emotion detection (frontend will stop sending frames automatically)
        # No need to stop background processes since we're not using them anymore

        # Save session data to Firebase with comprehensive emotion data
        try:
            user_email = session_data['user_email']

            # Get this session's own emotion data
            metrics = session_data['metrics']
            times = list(metrics['times'])
            emotions_data = {emotion: list(values)
                             for emotion, values in metrics['emotions'].items()}

            freq_times = list(metrics['freq_times'])
            frequencies = list(metrics['frequences'])

            # Create comprehensive session record
            session_record = {
                'user_email': user_email,
                'user_name': session_data['user_name'],
                'audio_file': session_data['audio_file'],
                'started_at': firestore.SERVER_TIMESTAMP,
                'finished_at': firestore.SERVER_TIMESTAMP,
                'duration': session_data['duration'],
                'times': times,
                'emotions': emotions_data,
                'freq_times': freq_times,
                'frequences': frequencies,
                'status': 'completed'
            }

            # Save to Firestore
            sessions_ref = db.collection('Sessions').document(
                user_email).collection('Records').document(session_id)
            sessions_ref.set(session_record)

            # Update history
            graph_db(session_id, user_email,
                     session_data['audio_file'], session_data['user_name'])

        except Exception as e:
            print(f"Error saving to Firebase: {e}")
            # Continue even if Firebase save fails

        # Clear this browser's session
        therapy_sessions.pop(session_id, None)
        session.pop('therapy_session_id', None)

        # Add a small delay to ensure data is saved to Firebase
        time.sleep(1)

        return jsonify({'success': True, 'message': 'Session completed and saved'})

    except Exception as e:
        print(f"Error ending session: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/contact_mail', methods=["POST", "GET"])
def contact_mail():
    if request.method == 'POST':
        result = request.form
        a = dict(result)
        print(a)
        html = f"""Dear Admin Team,
<br><br>
We have received a new submission from the "Reach Out to Us" form on our website.<br> Below are the details:
<br><br>
    &nbsp;&nbsp;&nbsp;&nbsp;<b>User Information</b> : <br>
    &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>- Name :</b> {a["fname"]} {a["lname"]}<br>
    &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>- Email Id :</b> {a["email"]}<br>
    &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>- Location :</b> {a["location"]}<br>
    &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>- Phone Number :</b> {a["phone_no"]}<br>
    &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>- Contact Choice :</b> {a["choice"]}<br>
    <br><br>
    &nbsp;&nbsp;&nbsp;&nbsp;<b>Message Details:</b><br>
    &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>- Message :</b> {a["message"]}
    <br><br>
Please review the submission and take the necessary actions to address the user’s request.
<br><br>
Thank you,
The SonicSerenity Team
"""
        recipient = "sonicserenity.therapy@gmail.com"
        subject = "New Contact Us Form Submission"
        msg = Message(
            subject, sender=app.config['MAIL_USERNAME'], recipients=[recipient])
        msg.html = html
        try:
            mail.send(msg)
        except Exception as e:
            print(e)
        if len(user_details):
            return render_template("contact_us.html", flask_login=flask_login, logged_in="yes", user_details=user_details, alert_msg="yes")
        else:
            return render_template("contact_us.html", flask_login=flask_login, logged_in="no", user_details=user_details, alert_msg="no")


if __name__ == '__main__':
    import multiprocessing
    import platform
    # Prefer 'fork' on macOS to avoid spawn-related issues with globals
    if platform.system() == 'Darwin':
        try:
            multiprocessing.set_start_method('fork')
        except RuntimeError:
            # Start method may already be set
            pass
    init_shared_state()
    # Disable reloader to prevent double-imports spawning extra processes
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5001)
