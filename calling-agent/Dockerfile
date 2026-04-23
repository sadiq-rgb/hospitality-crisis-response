# Start from official Python 3.11 image (slim = smaller size)
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy requirements first — this lets Docker cache the pip install
# layer so rebuilds are faster if only main.py changes
COPY requirements.txt .

# Install all Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code
COPY main.py .

# Tell the container to run main.py when it starts
CMD ["python", "main.py"]
