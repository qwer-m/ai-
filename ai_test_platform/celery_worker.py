from celery_config import celery_app

# Import modules containing tasks to ensure they are registered
import modules.tasks

if __name__ == "__main__":
    celery_app.start()
