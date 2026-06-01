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

# Copy agent source: definition, prompt, entry point
COPY agent_definition.py .
COPY root_agent_prompt.md .
COPY main.py .

# Bundle frontend (copied in by Cloud Build step copy-frontend-agent before this image builds)
COPY frontend ./frontend

EXPOSE 8080

CMD ["python", "main.py"]
