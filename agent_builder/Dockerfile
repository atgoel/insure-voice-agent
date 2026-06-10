# InsureVoice — ADK Agent Runner
# Cloud Run container image
#
# Build context: agent_builder/ directory
# Entry point:   main.py  (FastAPI + ADK Runner)
# Port:          8080 (Cloud Run default)

FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first (layer cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent source: definition, prompts, entry point + Day 5/6 modules
COPY agent_definition.py .
COPY root_agent_prompt.md .
COPY sub_agent3_explainer_prompt.md .
COPY main.py .
# Day 5 — P.2 conversational intake state machine (8-field validators)
COPY intake.py .
# Day 6 — S2'/S3 module-level session-state dicts + S3 follow-up state machine
COPY shared_state.py .
COPY followup.py .
# Day 8 — Tier B voice-stack modules (B1 TTS, B2 STT, B4 intent classifier)
COPY tts_streaming.py .
COPY stt_websocket.py .
COPY intent_classifier.py .

# Bundle frontend (copied in by Cloud Build step copy-frontend-agent before this image builds)
COPY frontend ./frontend

EXPOSE 8080

CMD ["python", "main.py"]
