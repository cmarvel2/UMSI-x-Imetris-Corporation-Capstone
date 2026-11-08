# UMSI-x-Imetris-Corporation-Capstone
This Data Engineering, Machine Learning and Analytics project focuses on transforming complex healthcare EDI (electronic data interchange) transactions (837 claims, 835 remittances, 834 enrollments) into structured, analyzable data and building predictive models to identify claim rejections and enrollment anomalies. 

## Current run steps all with windows command prompt:

### Current Architecture Diagram:
*data modeling is tentative, diagram shows high level example*

![alt text](image.png)

### Prerequisites:
- project requires python 3.14+ to run 

- run ```wsl --install``` from a command line terminal with admin privileges to install WSL 2 for docker, then restart windows.

- Download Docker here: https://www.docker.com/products/docker-desktop/ docker allows for us to package airflow and Postgresql together and run seamlessly on each of our laptops/development devices locally so you won't need to download postgresql or apache airflow locally. The database and apache airflow will only be accessible when the docker containers are running. In settings under resources make sure it says: "You are using the WSL 2 backend, so resource limits are managed by Windows."


- Download uv with ```pip install uv```

- Download python 3.14 if you don't have it with: ```uv python install 3.14```

- using the .env.example follow the directions within file to create your own .env that the docker container will use to create your local postgreSQL

### Project Setup Commands to run:
*commands below must run from project root where docker-compose.yaml is*

- create the venv and sync dependencies/libraries with ```uv sync --locked```

- Run ```uv run setup_landing_folders.py``` to setup the folders that edi file data will land in.

- Run ```docker compose up --build -d``` to build and start the docker images that airflow & our code along with the postgres database will run on in the background

### When developing
- Run ```docker compose up -d``` to start & restart containers again without rebuilding images with any current code changes

- Run ```docker compose up --build -d``` to rebuild and run containers whenever you have locally made or have done a git pull, changes inside src, changing dependencies, etc...

- Run ```docker compose stop``` to stop containers from running temporarily

### accessing postgres
*docker container must be running*
    I recommend this extension for accessing the database: https://marketplace.visualstudio.com/items?itemName=ms-ossdata.vscode-pgsql 

    server name: localhost
    username: same as .env
    password: same as .env
    database name: healthcare_edi
    under advanced:
        port: 5433

### accessing airflow
*docker container must be running*
    Press ctrl+shift+P and select Browser: Open Integrated Browser or use a browser of your choice
    http://localhost:8080/ enter this into the search bar (You could also just ctrl+click that link)
    username: airflow
    password: airflow