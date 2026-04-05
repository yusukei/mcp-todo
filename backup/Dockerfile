FROM mongo:7

RUN apt-get update && \
    apt-get install -y --no-install-recommends zip cron && \
    rm -rf /var/lib/apt/lists/*

COPY backup.sh /usr/local/bin/backup.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/entrypoint.sh

RUN mkdir -p /backups

CMD ["/usr/local/bin/entrypoint.sh"]
