# Copyright is waived. No warranty is provided. Unrestricted use and modification is permitted.

import os
import sys
import json
import csv
import struct
import xml.etree.ElementTree as ET

PURPOSE = '''\
Convert various text-based object formats to/from a compact binary format

Usage: binary-object.py <input_path> <output_path>
Options:
 <input_path>   path to input file
 <output_path>  path to output file
 
input and output types are determined from file extensions and may be any of;
.json    JSON file
.xml     XML file
.csv     CSV file
.bf      Binary format - a simple, flexible and extensible binary format
'''


class BinaryFormat:

    # Data types are stored in a variable length bit field
    TYPE_BIT_LENGTH = 3
    TYPE_BIT_STEP = 0

    TYPE_EMPTY = 0x00
    TYPE_BOOL = 0x01
    TYPE_INTEGER = 0x02
    TYPE_REAL = 0x03
    TYPE_BYTES = 0x04
    TYPE_UTF8 = 0x05
    TYPE_LIST = 0x06
    TYPE_UNIFORM_LIST = 0x07
    TYPE_MAP = 0x08
    TYPE_PROPERTIES = 0x09
    TYPE_COMMENT = 0x0A


class BinaryWriter:
    def __init__(self):
        self.data = bytearray()
        self.bits = 0
        self.bit_shift = 0
        self.bit_write_position = None
        self.string_map = {}
        self.string_list = []

    def write_byte(self, value):
        self.data.append(value)

    def write_bytes(self, data):
        self.data += data

    def write_bit(self, value):
        self.write_bits(1, value)

    def write_bits(self, num_bits, value):
        for i in range(num_bits//8):
            self.data.append(value & 0xff)
            value >>= 8

        num_bits = num_bits & 0x07
        if num_bits > 0:
            self.bits |= value << self.bit_shift

            if self.bit_shift == 0:
                self.bit_write_position = len(self.data)
                self.data.append(0)
                self.bit_shift = num_bits
            elif (self.bit_shift + num_bits) > 7:
                self.data[self.bit_write_position] = self.bits & 0xff
                self.bits >>= 8
                self.bit_shift = self.bit_shift + num_bits - 8
                if self.bit_shift > 0:
                    self.bit_write_position = len(self.data)
                    self.data.append(0)
            else:
                self.bit_shift += num_bits

    def write_integer(self, value):
        # Integers are stored in a variable length format
        while True:
            if value < 256:
                self.write_bits(2, 0)
                self.write_byte(value)
                break
            elif value < 256*256:
                self.write_bits(2, 1)
                self.write_byte(value & 0xff)
                self.write_byte(value >> 8)
                break
            elif value < 256*256*256:
                self.write_bits(2, 2)
                self.write_byte(value & 0xff)
                self.write_byte((value >> 8) & 0xff)
                self.write_byte(value >> 16)
                break
            else:
                self.write_bits(2, 3)
                self.write_byte(value & 0xff)
                self.write_byte((value >> 8) & 0xff)
                self.write_byte((value >> 16) & 0xff)
                value >>= 24

    def write_ieee754_2_64(self, value):
        value = struct.pack('<d', value)
        self.write_bytes(value)

    def write_string(self, string):
        if string in self.string_map:
            self.write_bit(1)                            # 1 = indexed string
            index = self.string_map[string]
            self.write_integer(index)
        else:
            self.write_bit(0)                            # 0 = literal string
            self.string_map[string] = len(self.string_map)
            string_bytes = string.encode('utf-8')
            self.write_integer(len(string_bytes))
            self.write_bytes(string_bytes)

    def write_variable_bits(self, bit_length, bit_step, value):
        max_value = 2**bit_length - 1
        while value >= max_value:
            self.write_bits(bit_length, max_value)
            value -= max_value
            if bit_step != 0:
                bit_length += bit_step
                max_value = 2**bit_length - 1
        self.write_bits(bit_length, value)

    def write_type(self, value):
        self.write_variable_bits(BinaryFormat.TYPE_BIT_LENGTH, BinaryFormat.TYPE_BIT_STEP, value)

    def finalize(self):
        # write any remaining bits
        if self.bit_shift > 0:
            self.data[self.bit_write_position] = self.bits & 0xff
            self.bits = 0
            self.bit_shift = 0


class BinaryReader:
    def __init__(self):
        self.data = None
        self.position = 0
        self.bits = 0
        self.bit_shift = 0
        self.string_map = {}
        self.string_list = []

    def read_byte(self):
        value = self.data[self.position]
        self.position += 1
        return value

    def read_bytes(self, length):
        value = self.data[self.position:self.position + length]
        self.position += length
        return value

    def read_bit(self):
        return self.read_bits(1)

    def read_bits(self, num_bits):
        value = 0
        num_bytes = num_bits // 8
        for i in range(num_bytes):
            value = value + (self.data[self.position] << (i*8))
            self.position += 1

        num_bits &= 0x07
        if num_bits > 0:
            if self.bit_shift == 0:
                self.bits = self.read_byte()
            elif self.bit_shift + num_bits > 8:
                self.bits += self.read_byte() << 8
            mask = (2**num_bits) - 1
            bit_value = (self.bits >> self.bit_shift) & mask
            self.bit_shift += num_bits
            if self.bit_shift >= 8:
                self.bits >>= 8
                self.bit_shift -= 8
            return value + (bit_value << (num_bytes*8))
        else:
            return value

    def read_integer(self):
        value = 0
        shift = 0
        while True:
            length = self.read_bits(2)
            if length == 0:
                value += self.read_byte() << shift
                break
            elif length == 1:
                value += (self.read_byte() << shift) + (self.read_byte() << (shift+8))
                break
            elif length == 2:
                value += (self.read_byte() << shift) + (self.read_byte() << (shift+8)) + (self.read_byte() << (shift+16))
                break
            else:
                value += (self.read_byte() << shift) + (self.read_byte() << (shift+8)) + (self.read_byte() << (shift+16))
                shift += 24
        return value

    def read_ieee754_2_64(self):
        value = struct.unpack('<d', self.data[self.position:self.position+8])[0]
        self.position += 8
        return value

    def read_string(self):
        is_indexed = self.read_bit()
        if is_indexed:
            index = self.read_integer()
            return self.string_list[index]
        else:
            length = self.read_integer()
            string = self.read_bytes(length).decode('utf-8')
            self.string_list.append(string)
            return string

    def read_variable_bits(self, bit_length, bit_step):
        value = 0
        max_value = 2**bit_length - 1
        next = self.read_bits(bit_length)
        while next == max_value:
            value += next
            if bit_step != 0:
                bit_length += bit_step
                max_value = 2**bit_length - 1
            next = self.read_bits(bit_length)
        value += next
        return value

    def read_type(self):
        return self.read_variable_bits(BinaryFormat.TYPE_BIT_LENGTH, BinaryFormat.TYPE_BIT_STEP)


class JSONEncoder(BinaryWriter):
    def __init__(self):
        BinaryWriter.__init__(self)

    @staticmethod
    def _json_parse_float(value):
        # TODO: provide an option to store real numbers as densely packed decimal
        # see https://en.wikipedia.org/wiki/Densely_packed_decimal
        return float(value)

    def encode(self, input_path):
        with open(input_path, 'r') as f:
            pyobj = json.load(f, parse_float=JSONEncoder._json_parse_float)
            self.encode_field(pyobj)
            self.finalize()
            return self.data

    def encode_field(self, value):
        if value is None:
            self.write_type(BinaryFormat.TYPE_EMPTY)
        elif isinstance(value, bool):
            self.write_type(BinaryFormat.TYPE_BOOL)
            self.write_bit(0 if value is False else 1)
        elif isinstance(value, int):
            self.write_type(BinaryFormat.TYPE_INTEGER)
            self.write_bit(0 if value >= 0 else 1)       # 0 if positive, 1 if negative
            self.write_integer(abs(value))
        elif isinstance(value, float):
            self.write_type(BinaryFormat.TYPE_REAL)
            self.write_ieee754_2_64(value)
        elif isinstance(value, bytes):
            self.write_type(BinaryFormat.TYPE_BYTES)
            self.write_integer(len(value))
            self.write_bytes(value)
        elif isinstance(value, str):
            self.write_type(BinaryFormat.TYPE_UTF8)
            self.write_string(value)
        elif isinstance(value, list):
            self.write_type(BinaryFormat.TYPE_LIST)
            self.write_integer(len(value))
            for child in value:
                self.encode_field(child)
        elif isinstance(value, dict):
            self.write_type(BinaryFormat.TYPE_MAP)
            self.write_integer(len(value))
            for child_key in value:
                self.encode_field(child_key)
                self.encode_field(value[child_key])
        else:
            raise ValueError('{0} type is unhandled'.format(type(value)))


class JSONDecoder(BinaryReader):
    def __init__(self):
        BinaryReader.__init__(self)

    def decode(self, binary_data):
        self.data = binary_data
        return self.decode_field()

    def decode_field(self, field_type=None):
        if not field_type:
            field_type = self.read_type()

        # JSON does not support properties
        while field_type == BinaryFormat.TYPE_PROPERTIES:
            num_items = self.read_integer()
            for i in range(num_items):
                key = self.decode_field()
                value = self.decode_field()
            field_type = self.read_type()

        # Decode primitive types
        if field_type == BinaryFormat.TYPE_EMPTY:
            return None
        elif field_type == BinaryFormat.TYPE_BOOL:
            return self.read_bit() == 1
        elif field_type == BinaryFormat.TYPE_INTEGER:
            is_negative = self.read_bit() == 1
            value = self.read_integer()
            return -value if is_negative else value
        elif field_type == BinaryFormat.TYPE_REAL:
            return self.read_ieee754_2_64()
        elif field_type == BinaryFormat.TYPE_BYTES:
            length = self.read_integer()
            return self.read_bytes(length)
        elif field_type == BinaryFormat.TYPE_UTF8:
            return self.read_string()
        elif field_type == BinaryFormat.TYPE_COMMENT:
            self.read_string()      # JSON does not support comments
            return None

        # Decode container types
        elif field_type == BinaryFormat.TYPE_LIST:
            values = []
            num_values = self.read_integer()
            for i in range(num_values):
                values.append(self.decode_field())
            return values

        elif field_type == BinaryFormat.TYPE_UNIFORM_LIST:
            values = []
            field_type = self.read_type()
            num_values = self.read_integer()
            for i in range(num_values):
                values.append(self.decode_field(field_type))
            return values

        elif field_type == BinaryFormat.TYPE_MAP:
            item_map = {}
            num_items = self.read_integer()
            for i in range(num_items):
                key = self.decode_field()
                if key:
                    value = self.decode_field()
                    if key in item_map:
                        if not isinstance(item_map[key], list):
                            item_map[key] = [item_map[key]]
                        item_map[key].append(value)
                    else:
                        item_map[key] = value
            return item_map

        else:
            raise ValueError('Unexpected data type')


class XMLCustomTreeBuilder(ET.TreeBuilder):
    def comment(self, data):
        # Preserve comments while parsing XML
        self.start(ET.Comment, {})
        self.data(data)
        self.end(ET.Comment)


class XMLEncoder(BinaryWriter):
    def __init__(self):
        BinaryWriter.__init__(self)

    def encode(self, input_path):
        parser = ET.XMLParser(target=XMLCustomTreeBuilder())
        root_node = ET.parse(input_path, parser=parser).getroot()
        # The top level container is a map with the root node as a single entry
        self.write_type(BinaryFormat.TYPE_MAP)
        self.write_integer(1)
        self.encode_node(root_node)
        self.finalize()
        return self.data

    def encode_node(self, node):
        if node.tag == ET.Comment:
            self.write_type(BinaryFormat.TYPE_COMMENT)
            self.write_string(node.text)
        else:
            # Write node tag as the item key
            self.write_type(BinaryFormat.TYPE_UTF8)
            self.write_string(node.tag)

            # Write node attributes
            if node.attrib:
                self.write_type(BinaryFormat.TYPE_PROPERTIES)
                self.write_integer(len(node.attrib))
                for attribute_key in node.attrib:
                    self.write_type(BinaryFormat.TYPE_UTF8)
                    self.write_string(attribute_key)
                    self.write_type(BinaryFormat.TYPE_UTF8)
                    self.write_string(node.attrib[attribute_key])

            # Write node contents; note, if the node contains children then the node text is ignored
            if len(node):
                self.write_type(BinaryFormat.TYPE_MAP)
                self.write_integer(len(node))
                for child in node:
                    self.encode_node(child)
            elif node.text:
                # Ignore node text if it's only whitespace
                text = node.text.strip()
                if text != '':
                    self.write_type(BinaryFormat.TYPE_UTF8)
                    self.write_string(text)
                else:
                    self.write_type(BinaryFormat.TYPE_EMPTY)
            else:
                self.write_type(BinaryFormat.TYPE_EMPTY)


class XMLDecoder(BinaryReader):
    def __init__(self):
        BinaryReader.__init__(self)

    def decode(self, binary_data):
        self.data = binary_data
        root_node = ET.Element('root')
        self.decode_node(root_node)
        return root_node[0]

    def decode_node(self, node):
        field_type = self.read_type()

        # Decode properties
        while field_type == BinaryFormat.TYPE_PROPERTIES:
            num_items = self.read_integer()
            for i in range(num_items):
                key = self.decode_field()
                value = self.decode_field()
                if node is not None:
                    node.attrib[key] = value
            field_type = self.read_type()

        # Decode container types
        if field_type == BinaryFormat.TYPE_MAP:
            num_items = self.read_integer()
            for i in range(num_items):
                field_type = self.read_type()
                if field_type == BinaryFormat.TYPE_COMMENT:
                    child_node = ET.Element(ET.Comment)
                    child_node.text = self.read_string()
                    node.append(child_node)
                else:
                    key = self.decode_field(field_type)
                    child_node = ET.Element(key)
                    self.decode_node(child_node)
                    node.append(child_node)

        elif field_type == BinaryFormat.TYPE_LIST:
            num_items = self.read_integer()
            for i in range(num_items):
                child_node = ET.Element('item')
                self.decode_node(child_node)
                node.append(child_node)

        elif field_type == BinaryFormat.TYPE_UNIFORM_LIST:
            # TODO: handle uniform list
            item_type = self.read_type()
            num_items = self.read_integer()
            for i in range(num_items):
                child_node = ET.Element('item')
                self.decode_node(child_node)
                node.append(child_node)

        else:
            value = self.decode_field(field_type)
            if value is not None:
                node.text = str(value)

    def decode_field(self, field_type=None):
        if field_type is None:
            field_type = self.read_type()
        if field_type == BinaryFormat.TYPE_EMPTY:
            return None
        elif field_type == BinaryFormat.TYPE_BOOL:
            return self.read_bit() == 1
        elif field_type == BinaryFormat.TYPE_INTEGER:
            is_negative = self.read_bit() == 1
            value = self.read_integer()
            return -value if is_negative else value
        elif field_type == BinaryFormat.TYPE_REAL:
            return self.read_ieee754_2_64()
        elif field_type == BinaryFormat.TYPE_BYTES:
            length = self.read_integer()
            return self.read_bytes(length)
        elif field_type == BinaryFormat.TYPE_UTF8:
            return self.read_string()
        else:
            raise ValueError('Unexpected data type')


class CSVEncoder(BinaryWriter):
    def __init__(self):
        BinaryWriter.__init__(self)

    def encode(self, input_path):
        # The top level container is a list of lists
        num_rows = 0
        with open(input_path, 'r', newline='') as f:
            for row in csv.reader(f):
                num_rows += 1
        self.write_type(BinaryFormat.TYPE_UNIFORM_LIST)
        self.write_type(BinaryFormat.TYPE_LIST)
        self.write_integer(num_rows)

        # Now write each row as a list
        with open(input_path, 'r', newline='') as f:
            for row in csv.reader(f):
                self.write_integer(len(row))
                for value in row:
                    if value.strip() == '':
                        self.write_type(BinaryFormat.TYPE_EMPTY)
                        continue

                    try:
                        int_value = int(value)
                        self.write_type(BinaryFormat.TYPE_INTEGER)
                        self.write_bit(0 if int_value >= 0 else 1)       # 0 if positive, 1 if negative
                        self.write_integer(abs(int_value))
                        continue
                    except ValueError:
                        pass

                    try:
                        float_value = float(value)
                        self.write_type(BinaryFormat.TYPE_REAL)
                        self.write_ieee754_2_64(float_value)
                        continue
                    except ValueError:
                        pass

                    # Write as a string
                    self.write_type(BinaryFormat.TYPE_UTF8)
                    self.write_string(value)
        self.finalize()
        return self.data


class CSVDecoder(BinaryReader):
    def __init__(self):
        BinaryReader.__init__(self)

    def decode(self, binary_data):
        self.data = binary_data
        return self.decode_field()

    def decode_field(self, field_type=None):
        if not field_type:
            field_type = self.read_type()

        # CSV does not support properties
        while field_type == BinaryFormat.TYPE_PROPERTIES:
            num_items = self.read_integer()
            for i in range(num_items):
                key = self.decode_field()
                value = self.decode_field()
            field_type = self.read_type()

        # Decode primitive types
        if field_type == BinaryFormat.TYPE_EMPTY:
            return ''
        elif field_type == BinaryFormat.TYPE_BOOL:
            return self.read_bit() == 1
        elif field_type == BinaryFormat.TYPE_INTEGER:
            is_negative = self.read_bit() == 1
            value = self.read_integer()
            return -value if is_negative else value
        elif field_type == BinaryFormat.TYPE_REAL:
            return self.read_ieee754_2_64()
        elif field_type == BinaryFormat.TYPE_BYTES:
            length = self.read_integer()
            return self.read_bytes(length)
        elif field_type == BinaryFormat.TYPE_UTF8:
            return self.read_string()
        elif field_type == BinaryFormat.TYPE_COMMENT:
            self.read_string()      # CSV does not support comments
            return None

        # Decode container types
        elif field_type == BinaryFormat.TYPE_LIST:
            output = ''
            num_values = self.read_integer()
            for i in range(num_values):
                output += str(self.decode_field())
                if i < num_values - 1:
                    output += ','
            return output

        elif field_type == BinaryFormat.TYPE_UNIFORM_LIST:
            output = ''
            field_type = self.read_type()
            num_values = self.read_integer()
            for i in range(num_values):
                output += str(self.decode_field(field_type))
                if i < num_values - 1:
                    output += '\n'
            return output

        elif field_type == BinaryFormat.TYPE_MAP:
            raise ValueError ('Map type is unsupported')

        else:
            raise ValueError('Unexpected data type')


def interchange(input_path, output_path):

    # Encode text to binary
    input_type = os.path.splitext(input_path)[1].lower().lstrip('.')
    if input_type == 'json':
        binary_data = JSONEncoder().encode(input_path)
    elif input_type == 'xml':
        binary_data = XMLEncoder().encode(input_path)
    elif input_type == 'csv':
        binary_data = CSVEncoder().encode(input_path)
    elif input_type == 'bf':
        with open(input_path, 'rb') as f:
            binary_data = f.read()
    else:
        raise ValueError('Unknown input type')

    # Encode binary to text
    output_type = os.path.splitext(output_path)[1].lower().lstrip('.')
    if output_type == 'json':
        pyobject = JSONDecoder().decode(binary_data)
        output_data = json.dumps(pyobject, indent=2)
    elif output_type == 'xml':
        xml_root = XMLDecoder().decode(binary_data)
        ET.indent(xml_root)
        output_data = ET.tostring(xml_root).decode('utf-8')
    elif output_type == 'csv':
        output_data = CSVDecoder().decode(binary_data)
    elif output_type == 'bf':
        output_data = binary_data
    else:
        raise ValueError('Unknown output type')

    if output_data:
        output_mode = 'wb' if output_type == 'bf' else 'w'
        with open(output_path, output_mode) as f:
            f.write(output_data)


if __name__ == '__main__':
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    if not (input_path and output_path):
        sys.exit(PURPOSE)
    interchange(input_path, output_path)
