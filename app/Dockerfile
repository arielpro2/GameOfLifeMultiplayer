# syntax=docker/dockerfile:1


FROM python:3.9

RUN apt-get clean \
    && apt-get -y update

RUN apt-get -y install \
    nginx \
    python3-dev \
    build-essential

WORKDIR /app

# If running locally, disable redis
ENV RUNNING_IN_KUBERNETES=false
RUN if [ -n "$KUBERNETES_SERVICE_HOST" ]; then \
        export DEBUG=false; \
    else \
        export DEBUG=true; \
    fi

COPY requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt --src /usr/local/src

COPY . .

EXPOSE 5000
CMD [ "python", "app.py" ]
