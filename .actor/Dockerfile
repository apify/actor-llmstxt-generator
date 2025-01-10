FROM apify/actor-python:3.12

WORKDIR /usr/src/app

COPY pyproject.toml poetry.lock pyproject.toml ./

ARG ACTOR_PATH_IN_DOCKER_CONTEXT
ENV ACTOR_PATH_IN_DOCKER_CONTEXT="${ACTOR_PATH_IN_DOCKER_CONTEXT}"

RUN echo "Python version:" \
    && python --version \
    && echo "Pip version:" \
    && pip --version \
    && echo "Installing Poetry:" \
    && pip install --no-cache-dir poetry~=1.8 \
    && echo "Installing dependencies:" \
    && poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --without dev \
    && rm -rf /tmp/.poetry-cache \
    && echo "All installed Python packages:" \
    && pip freeze

COPY src ./src

CMD ["python3", "-m", "src"]
