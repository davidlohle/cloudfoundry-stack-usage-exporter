# Prometheus Exporter for CF Stack Usage

Looking to gain insights into distribution of stacks throughout a CF
installation? This exporter scrapes the CF API to generate a metric called
`cf_stack_count` with the label of `stack` to identify the count of applications
using each stack installed on a CF installation.

It's meant to be run as a cf app, but can easily run wherever you've got Python.

## Installation

Take a look at `manifest.yml`: you'll need three bits of information:

`CF_SYS_HOST` is the hostname prefix for your CF's system domain (i.e. `sys.cloud.seventhprotocol.com`) 
`CF_USERNAME` is an UAA account that has at minimum read-only permissions to all of the `/v3/apps` endpoint 
`CF_PASSWORD` is the password to the account above.

Optionally, you can also configure `SCRAPE_INTERVAL` which dictates how often
the background task of refreshing the metric occurs. By default it's 300
seconds.


An example CF push:

```
cf push \
--var cf_sys_hostname=sys.cloud.seventhprotocol.com \
--var cf_username=readonly \
--var cf_password=deepbluesea
```

If your CF installation doesn't allow downloading dependencies from the internet
(common), run this command to vendor the dependencies prior to pushing: 

```
pip3 download \
--only-binary=:all: \
--python-version=3.12 \
--platform=manylinux2014_x86_64 \
--dest=vendor \
--requirement=requirements.txt
```