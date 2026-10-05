from fastapi import FastAPI

app = FastAPI(title="SRE Demo Frontend")


@app.get("/")
def home():
    return {
        "application": "SRE Incident Investigator Demo",
        "frontend": "running",
        "message": "Welcome to the SRE demo application"
    }


@app.get("/health")
def health():
    return {
        "service": "frontend",
        "status": "healthy"
    }
