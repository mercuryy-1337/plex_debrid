FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV TERM=xterm
ENV PYTHONUNBUFFERED=1

# NiceGUI web UI port
EXPOSE 8008

# Default: web UI mode with config in /app/config
CMD ["python", "./main.py", "--config-dir", "config", "--host", "0.0.0.0", "--port", "8008"]
