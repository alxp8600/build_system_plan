FROM python:3.12-alpine
WORKDIR /app
RUN pip install fastapi uvicorn[standard] httpx itsdangerous psycopg[binary] boto3 cryptography
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]