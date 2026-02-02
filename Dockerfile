FROM python:3.13-slim

RUN apt-get update && apt-get -y install jq && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /action/entrypoint.sh
COPY generate_releasenotes.py /action/generate_releasenotes.py
COPY requirements.txt /action/requirements.txt

RUN pip3 install --no-cache-dir -r /action/requirements.txt

RUN chmod +x /action/entrypoint.sh /action/generate_releasenotes.py

ENTRYPOINT ["/action/entrypoint.sh"]
