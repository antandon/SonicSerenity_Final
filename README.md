# SonicSerenity

A sound-therapy web app that reads your emotions through your webcam and plays music tuned to help you feel better. Start a session, let the app watch your facial expressions, and the background track is pitch-shifted toward a solfeggio frequency matched to the emotion it detects. Every session is saved so you, and a therapist you choose to share with, can review how your mood changed over time.

## Features

- **Live emotion detection**: webcam frames are analysed with AWS Rekognition to detect anger, fear, happiness, sadness or a neutral state in real time
- **Emotion-based sound therapy**: the session's music is pitch-shifted toward a solfeggio frequency for the detected emotion (for example 417 Hz for anger, 285 Hz for fear, 639 Hz for happiness, 174 Hz for sadness)
- **Upload your own track**: the app finds a track's dominant frequency with Librosa and uses it as the starting point for pitch shifting
- **Session history & graphs**: emotion and frequency changes over each session are charted with Bokeh
- **User and therapist roles**: users can share selected sessions with a therapist, and therapists can view their patients' shared sessions and graphs
- **Google sign-in** via OAuth
- **TonicSounds**: an embedded Hugging Face Space for generating therapeutic music
- **Information pages**: services, approach, about us, FAQ, and a contact form that sends email

## How it works

1. Sign in with Google as a user or a therapist.
2. Choose or upload a track and start a session.
3. Your browser sends webcam frames to the server, which detects your emotion with AWS Rekognition.
4. The server pitch-shifts the track toward the solfeggio frequency for that emotion and streams it back.
5. Emotion and frequency data are stored in Firestore as the session runs.
6. After the session, view your graphs and, if you like, share them with a therapist.

## Tech stack

- **Backend**: Python, Flask, Flask-Login, Flask-Mail
- **Frontend**: Jinja HTML templates, CSS, JavaScript
- **Data**: Firebase (Firestore for users, sessions and emotion data; Cloud Storage for audio)
- **Auth**: Google OAuth
- **AI & audio**: AWS Rekognition (emotion detection), Librosa and NumPy (frequency analysis and pitch shifting), OpenCV
- **Charts**: Bokeh, Matplotlib

## Getting started

Requires Python 3.13 or newer.

```sh
python -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python new_therapy.py
```

The app runs at http://localhost:5001.

### Credentials

Credentials are not included in this repository. You need your own:

- **Firebase**: create a project with Firestore and Cloud Storage enabled, generate a service account key (Project Settings → Service Accounts), and save it in the project folder as `firebase-adminsdk.json`. Update the `storageBucket` name in `new_therapy.py` to your own bucket.
- **Google OAuth**: create an OAuth client in Google Cloud Console and save its JSON file as `client_secret.json`.
- **AWS**: an access key for an IAM user allowed to call Rekognition.

Then create a `.env` file in the project folder:

```
FLASK_SECRET_KEY=

# Google OAuth
GOOGLE_CLIENT_ID=
OAUTH_REDIRECT_URI=http://localhost:5001/callback

# AWS Rekognition
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1

# Contact form email (Gmail SMTP; use an app password)
MAIL_USERNAME=
MAIL_PASSWORD=

# Optional: custom paths to the credential files
# FIREBASE_CREDENTIALS=firebase-adminsdk.json
# GOOGLE_CLIENT_SECRETS_FILE=client_secret.json
```

`.env` and both JSON credential files are git-ignored. Never commit them.
