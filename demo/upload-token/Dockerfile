FROM python:3.12-alpine
WORKDIR /app
RUN pip install fastapi uvicorn[standard] httpx psycopg[binary] pyjwt[crypto] boto3
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]