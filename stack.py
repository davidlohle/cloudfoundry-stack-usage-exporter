from flask import Flask, Response
from prometheus_client import CollectorRegistry, Gauge, generate_latest
import time
import requests
import os
import sys
import concurrent.futures
import threading
import logging



requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

"""
This Python Flask web app connects to the TAS API and generates a Prometheus metric called `cf_stack_count`.
The label `stack` dictates a count of how many applications are running that stack.

The metrics gathering occurs in a threaded loop as it takes awhile (~10-20 seconds) which is too long
for Prometheus to wait for, so we instead serve the latest metrics that have been gathered from the loop.

This Flask app needs three environment variables:

CF_SYS_DOMAIN is the sys prefix for whatever CF you'd like to scrape.
CF_USERNAME is an account that has permissions to access /v3/apps (admin is a good one)
CF_PASSWORD is the password to the above account

A fourth optional environment variable is:
SCRAPE_INTERVAL which dictates how often we hit the CF API for updated metrics in seconds. By default it's 300 seconds (5 minutes)
"""



app = Flask(__name__)
registry = CollectorRegistry()

logging.getLogger("werkzeug").disabled = True
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(stream=sys.stdout, format='[%(levelname)s] [%(name)s] %(message)s',level=log_level)
logger = logging.getLogger("StackExporter")

REQUIRED_ENV_VARS = ["CF_SYS_HOSTNAME", "CF_USERNAME", "CF_PASSWORD"]

CF_SYS_DOMAIN = os.getenv("CF_SYS_HOSTNAME")
CF_API_URL = f"https://api.{CF_SYS_DOMAIN}"
CF_UAA_URL = f"https://uaa.{CF_SYS_DOMAIN}"

CF_AUTH_TOKEN = ""
CF_USERNAME = os.getenv("CF_USERNAME")
CF_PASSWORD = os.getenv("CF_PASSWORD")

SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "300"))
PORT = os.getenv("PORT", "8080")

stack_gauge = Gauge("cf_stack_count", "Total number of apps using each stack", ["stack"], registry=registry)
stack_cache = {}
cache_lock = threading.Lock()

valid_stacks = []

def validate_env_vars():
    missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing_vars:
        logger.fatal(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)


def grab_valid_stacks():
    stacks_endpoint = f"{CF_API_URL}/v3/stacks"
    stack_list = api_call(stacks_endpoint)

    for stack in stack_list['resources']:
        logger.debug(f"Adding {stack} to valid list")
        valid_stacks.append(stack['name'])
    
    logger.info(f"Valid stack list is: {valid_stacks}")


def get_token():
    auth_endpoint = f"{CF_UAA_URL}/oauth/token"
    headers = {"Accept": "application/json", 
               "Authorization": "Basic Y2Y6",
               "Content-Type'": "application/x-www-form-urlencoded"
               }
    global CF_AUTH_TOKEN

    response = requests.post(auth_endpoint, data={
        "grant_type": "password",
        "client_id": "cf",
        "username": CF_USERNAME,
        "password": CF_PASSWORD
    }, headers=headers, verify=False)


    response.raise_for_status()
    CF_AUTH_TOKEN = response.json()["access_token"]

    logger.debug(f"Token fetched: {CF_AUTH_TOKEN}")
    return


def api_call(url):
    global CF_AUTH_TOKEN
    headers = {"Authorization": f"Bearer {CF_AUTH_TOKEN}"}
    logger.debug(f"Fetching API: {url}")
    response = requests.get(url, headers=headers, verify=False)
    response.raise_for_status()
    return response.json()


def generate_stack_metrics():
    global stack_cache
    while True:
        try:
            apps_endpoint = f"{CF_API_URL}/v3/apps"
            stack_counts = {}
            apps_count = 0
            retries = 0

            while apps_count == 0:
                if retries >= 5:
                    logger.error(f"Quitting iteration after 5 attempts to authenticate to UAA.")
                    raise Exception
                try:
                    apps_count = api_call(apps_endpoint)
                except requests.exceptions.RequestException as err:
                    if err.response is None:
                        logger.error(f"Error while enumerating applications: {err}")
                        time.sleep(5)
                        retries += 1
                        continue
                    elif err.response.status_code == 403 or err.response.status_code == 401:
                        logger.error(f"Token not valid, attempting re-login")
                        get_token()
                        time.sleep(5)
                        retries += 1
                        continue
                    else:
                        logger.error(f"Error while enumerating applications: {err}")
                        time.sleep(5)
                        retries += 1
                        continue
                except Exception as err:
                        logger.error(f"Error while enumerating applications: {err}")
                        time.sleep(5)
                        retries += 1
                        continue
                    

            logger.debug(f"Pagination data: {apps_count["pagination"]}")
            apps_count = apps_count["pagination"]["total_pages"]
            logger.debug(f"There are {apps_count} pages to iterate through")

            all_urls = []
            for page in range(1, apps_count+1):
                all_urls.append(f"{apps_endpoint}?page={page}&per_page=50")

            logger.debug(f"Total URLs mapped for threading: {all_urls}")
            with concurrent.futures.ThreadPoolExecutor() as exec:
                responses = list(exec.map(api_call, all_urls))

            for response in responses:
                for app in response.get("resources", []):
                    stack = app.get("lifecycle", {}).get("data", {}).get("stack")
                    if stack:
                        if stack in valid_stacks:
                            stack_counts[stack] = stack_counts.get(stack, 0) + 1
                        else:
                            logger.debug(f"Discarding {stack} as it's not in list: {valid_stacks}")
            
            logger.info(f"Current metrics: {stack_counts}")
            with cache_lock:
                stack_cache = stack_counts

        except Exception as err:
            logger.error(f"Error while fetching metrics: {err}")
            time.sleep(SCRAPE_INTERVAL)
            continue
        
        logger.info("Successfully scraped CF API")
        time.sleep(SCRAPE_INTERVAL)

@app.route("/metrics")
def metrics():
    local_cache = {}

    with cache_lock:
        local_cache = stack_cache.copy()

    for stack, count in local_cache.items():
        stack_gauge.labels(stack=stack).set(count)
    
    return Response(generate_latest(registry), mimetype="text/plain")


if __name__ == "__main__":
    validate_env_vars()
    get_token()
    grab_valid_stacks()
    threading.Thread(target=generate_stack_metrics, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)