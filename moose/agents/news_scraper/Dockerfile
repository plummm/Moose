# Auto-generated Dockerfile for Moose Agent
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system packages if specified


# Install Moose package
# The build context should be the project root, so moose/ and setup.py are available
# Copy the entire moose package
COPY moose /tmp/moose/moose
COPY setup.py /tmp/moose/
COPY requirements.txt /tmp/moose/requirements.txt
RUN pip install --no-cache-dir /tmp/moose && rm -rf /tmp/moose

# Copy agent files (from moose/agents/<agent_name>/)
COPY moose/agents/news_scraper /app

# Run setup script if it exists


# Install Python dependencies if requirements.txt exists
RUN pip install --no-cache-dir -r requirements.txt

# Set entry point
CMD ["python", "entry.py"]

