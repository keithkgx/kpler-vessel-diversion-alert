FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements-worker.txt ./
RUN python -m pip install --no-cache-dir -r requirements-worker.txt

COPY gsheet_handler.py kpler_handler.py shipment_update_flagging_workflow.py ./
CMD ["python", "shipment_update_flagging_workflow.py"]
