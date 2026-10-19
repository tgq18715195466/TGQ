FROM python:3.12-slim-bookworm
WORKDIR /srv/scanner
COPY scanner_shim.py ./
EXPOSE 8080
CMD ["python", "-u", "scanner_shim.py"]
