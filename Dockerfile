# Use an official lightweight Python stable runtime as a parent image
FROM python:3.11-slim

# Prevent Python from writing .pyc files to disc and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy only requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install dependencies (system-wide inside the container is standard/safe)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source code and static assets into the container
COPY database.py main.py tracing.py ./
COPY static/ ./static/

# Expose the default listening port
EXPOSE 8080

# Run the service. 
# We run `python main.py` to leverage our custom command line argument parsing 
# and fallback URL logic, enabling users to pass --base-url or other parameters easily.
CMD ["uvicorn", "main:app"]
