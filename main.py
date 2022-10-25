import os
import pprint
import time
import json
import confuse
import logging
import platform
import collections
import paho.mqtt.client
import prometheus_client
# from pprint import pprint
from modbus import Modbus
from queue import Queue
from threading import Thread
from time import sleep
import paho.mqtt.client as mqtt

config = None  # type: confuse.Configuration


def load_config():
    global config
    config = confuse.Configuration('m2me')
    config.set_file('m2me.yaml', False)
    config.set_env()
    config['mqtt']['password'].redact = True


def main():
    load_config()
    args = {}

    for key in config['logging']['config'].keys():
        args[key] = config['logging']['config'][key].get()

    logging.basicConfig(**args)
    logging.info('m2me starting')

    if 'level' in args.keys():
        logger = logging.getLogger()
        logger.setLevel(args['level'])

    if config['logging'].get().get('enabled', False):
        logger.setLevel(logging.CRITICAL)

    if bool(config['prometheus'].get().get('enabled', False)):
        prometheus_client.start_http_server(int(config['prometheus'].get().get('port', 9091)))

    try:
        mqtt_config = config['mqtt'].get()
        mqttc = mqtt.Client(protocol=paho.mqtt.client.MQTTv5, client_id='m2me-{0}-{1}'.format(platform.node(), os.getpid()))
        mqttc.username_pw_set(username=mqtt_config.get('username', ''), password=mqtt_config.get('password', ''))
        mqttc.connect_async(host=mqtt_config.get('host', 'localhost'), port=mqtt_config.get('port', 1883), keepalive=60)
        mqttc.loop_start()
    except Exception as e:
        logger.info('I was unable to setup a mqtt connect ({})'.format(e))
        exit(1)

    bus_queue = Queue()
    t = Thread(target=query_bus, name="query_bus", args=(bus_queue,))
    t.start()
    device_data = {}
    prometheus_gauges = {}
    mqtt_topics = {}

    while True:
        if not bus_queue.empty():
            data = bus_queue.get()

            for key in data['items'].keys():
                item = data['items'][key]['item']
                unit = data['device']['unit']

                if unit not in device_data:
                    device_data[unit] = {}

                device_data[unit][key] = data['items'][key]['value']

                if unit not in mqtt_topics:
                    mqtt_topics[unit] = {}
                if key not in mqtt_topics[unit]:
                    mqtt_topics[unit][key] = 0
                if mqtt_topics[unit][key] >= 100:
                    logging.info('Republishing mqtt metrics')
                    mqtt_topics[unit][key] = 0

                if mqtt_topics[unit][key] == 0:
                    topic_prefix = '{0}/sensor/{1}/{2}_{3}'.format(
                        mqtt_config.get('discovery_prefix', 'homeassistant'),
                        '{0}'.format(platform.node()),
                        unit, key
                    )
                    payload = {
                        'state_topic': 'm2me/{0}/{1}'.format(platform.node(), unit),
                        'name': key,
                        'uniq_id': '{0}-{1}-{2}'.format(platform.node(), unit, key),
                        'device_class': item.get('device_class', ''),
                        'state_class': item.get('state_class', 'measurement'),
                        'unit_of_measurement': item.get('unit_of_measurement', ),
                        'value_template': '{{{{ value_json[\'{0}\'] | default(0, true) }}}}'.format(key),
                        'device': {
                            'name': '{0}-{1}'.format(platform.node(), unit),
                            'model': 'Unit: {0}'.format(unit),
                            'manufacturer': 'm2me',
                            'identifiers': [
                                '{0}-{1}'.format(platform.node(), unit),
                            ]
                        }
                    }
                    msg = mqttc.publish(
                        topic='{0}/config'.format(topic_prefix),
                        payload=json.dumps(payload),
                        retain=True,
                    )

                if config['prometheus'].get().get('enabled', False):
                    gauge = None
                    if key in prometheus_gauges:
                        gauge = prometheus_gauges[key]

                    if not gauge:
                        gauge = prometheus_client.Gauge(
                            namespace='m2me', name=str(key), documentation='', labelnames=['unit', 'proxy']
                        )
                        prometheus_gauges[key] = gauge

                    gauge.labels(unit=unit, proxy=platform.node()).set(device_data[unit][key])

                if int(item.get('multiply', 0)) > 0:
                    device_data[unit][key] *= int(item.get('multiply', 1))
                if int(item.get('divide', 0)) > 0:
                    device_data[unit][key] /= int(item.get('divide', 1))
                if int(item.get('precision', 0)) > 0 and type(device_data[unit][key]) == float:
                    device_data[unit][key] = round(device_data[unit][key], int(item.get('precision', 0)))

                mqttc.publish(
                    topic='m2me/{0}/{1}'.format(platform.node(), unit),
                    payload=json.dumps(device_data[unit]),
                    retain=False,
                )

                mqtt_topics[unit][key] += 1

    mqttc.loop_stop()
    t.join()


def query_bus(queue: Queue):
    modbus = Modbus(config)

    while True:
        for device in config['modbus']['devices'].get():
            data = {'device': device, 'items': None}

            try:
                data['items'] = modbus.query_device(device)
            except Exception as err:
                logging.debug('Exception in device query ({}), continue'.format(err))

            queue.put(data)
        time.sleep(config['modbus'].get().get('sleep', 0))


if __name__ == '__main__':
    main()
