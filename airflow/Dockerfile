FROM apache/airflow:2.10.5-python3.12
# Las constraints de Airflow fijan SQLAlchemy 1.4 y versiones de pandas compatibles con ella;
# sin ellas pip puede instalar un pandas que no funciona con la SQLAlchemy de Airflow.
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.12.txt"
