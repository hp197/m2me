import collections

import confuse
import pymodbus.client.sync
import pymodbus.register_read_message
from pymodbus.payload import BinaryPayloadDecoder
import logging


class Modbus:
    client = None  # type: pymodbus.client.sync.ModbusSerialClient
    config = None

    functions = {
        'input': 'read_input_registers',
        'coils': 'read_coils',
        'holding': 'read_holding_registers',
        'discrete': 'read_discrete_inputs',
        'exception': 'read_exception_status',
    }

    def __init__(self, config: confuse.ConfigView):
        self.config = config
        self.__connect()

    def __connect(self):
        args = {'method': 'rtu'}
        for key in self.config['modbus']['pymodbus_args'].keys():
            args[key] = self.config['modbus']['pymodbus_args'][key].get()

        self.client = pymodbus.client.sync.ModbusSerialClient(**args)
        self.client.connect()

    def close(self):
        logging.debug('Closing bus')
        return self.client.close()

    def query_device(self, device: collections.OrderedDict):
        unit = device.get('unit', None)
        if not unit:
            raise Exception('unit is not set for device')
        logging.debug('Modbus::query_device {0}'.format(device.get('name', unit)))

        data = {}
        for item in device.get('items', {}):
            key = item.get('name', 'address[{0}]'.format(int(item.get('address', 0))))

            try:
                data[key] = self.__query_item(device, item)
            finally:
                continue

        return data

    def __query_item(self, device: collections.OrderedDict, item: collections.OrderedDict):
        unit = int(device.get('unit', 1))
        function = self.__get_function(item.get('function', None))

        if hasattr(self.client, function) and callable(func := getattr(self.client, function)):
            data = func(address=int(item.get('address', 0)), count=int(item.get('count', 2)), unit=unit)  # type: pymodbus.register_read_message.ReadHoldingRegistersResponse
        else:
            raise Exception('Could not call pymodbus function {0}'.format(function))

        if data.isError():
            raise Exception('Got type {0}'.format(type(data)))
        if len(data.registers) != item.get('count', 2):
            raise Exception('Got wrong datacount {0}!=={1}'.format(len(data.registers), int(item.get('count', 2))))

        byteorder = device.get('byteorder', '>') # big edian by default
        wordorder = device.get('wordorder', '>') # big endian by default

        decoder = BinaryPayloadDecoder.fromRegisters(registers=data.registers, wordorder=wordorder, byteorder=byteorder)

        function = 'decode_{0}bit_{1}'.format(int(item.get('count', 2)) * 16, str(item.get('formatter', 'float')).lower())
        if hasattr(decoder, function) and callable(func := getattr(decoder, function)):
            value = func()
        else:
            raise Exception('Unknown formatter {0}'.format(function))

        return {'value': value, 'item': item}

    def __get_function(self, function):
        return self.functions[function]
