import binascii
import random
import string
import struct

import ipaddress

from DNSpy.enums import DnsQType, DnsRClass, DnsQClass, DnsRType, DnsQR, DnsOpCode, DnsResponseCode


class DnsQuestion(object):
    def __hash__(self):
        return hash((self.name, self.qtype, self.qclass))

    def __init__(self, qname, qtype=DnsQType.ANY, qclass=DnsRClass.IN):
        self.name = qname
        self.qtype = qtype
        self.qclass = qclass

    @staticmethod
    def parse(data, offset):
        (name, offset) = DomainName.parse_from(data, offset)
        (qtype, qclass,) = struct.unpack_from('!HH', data, offset)
        offset += 4

        try:
            qtype = DnsQType(qtype)
            qclass = DnsQClass(qclass)
        except ValueError:
            pass

        return (DnsQuestion(name, qtype, qclass), offset,)

    def __repr__(self):
        return "<DnsQuestion:%s,%s,%s>" % (self.name, self.qtype, self.qclass)

    def encode(self):
        return self.name.encode() + struct.pack('!HH', self.qtype, self.qclass)


class RData(object):
    def __init__(self, blob):
        self.blob = blob

    @classmethod
    def parse(cls, blob):
        return cls(blob)

    @classmethod
    def get_handler(cls, rtype):
        handlers = {DnsRType.SOA: RData_SOA,
                    DnsRType.NS: RData_SingleName,
                    DnsRType.CNAME: RData_SingleName,
                    DnsRType.PTR: RData_SingleName,
                    DnsRType.A: RData_A,
                    DnsRType.AAAA: RData_AAAA,
                    }

        if rtype in handlers:
            return handlers[rtype]
        else:
            return cls

    def __repr__(self):
        return binascii.b2a_hex(self.blob).decode('ascii')


class RData_SOA(RData):
    def __init__(self, serial, refresh, retry, expire, mname, rname):
        self.serial = serial
        self.refresh = refresh
        self.retry = retry
        self.expire = expire
        self.mname = mname
        self.rname = rname

    @classmethod
    def parse(cls, rdata, offset=0):
        # TODO: consider some wizardry with locals()
        (mname, offset) = DomainName.parse_from(rdata, offset)
        (rname, offset) = DomainName.parse_from(rdata, offset)
        (serial, refresh, retry, expire) = struct.unpack_from('!IIII', rdata, offset)
        return cls(serial, refresh, retry, expire, mname, rname)

    def encode(self):
        return self.mname.encode() + self.rname.encode() + struct.pack('!IIII',
                                                                       self.serial,
                                                                       self.refresh,
                                                                       self.retry,
                                                                       self.expire)
    def __repr__(self):
        return "%d %d %d %d %s %s" % (self.serial, self.refresh, self.retry, self.expire, self.mname, self.rname)


class RData_SingleName(RData):
    @classmethod
    def parse(cls, rdata, offset=0):
        (name, offset) = DomainName.parse_from(rdata, offset)
        return cls(name)

    def __init__(self, name):
        self.name = name

    def encode(self):
        return self.name.encode()

    def __repr__(self):
        return repr(self.name)

class RData_A(RData):
    def __init__(self, ip):
        assert isinstance(ip, ipaddress.IPv4Address)
        self.ip = ip

    @classmethod
    def parse(cls, rdata, offset=0):
        ip = ipaddress.ip_address(rdata)
        return cls(ip)

    def encode(self):
        return self.ip.packed

    def __repr__(self):
        return self.ip.exploded

class RData_AAAA(RData_A):
    def __init__(self, ip):
        assert isinstance(ip, ipaddress.IPv6Address)
        self.ip = ip
        #TODO: super

class DnsRecord(object):
    def __init__(self, name, rtype=DnsRType.A, rclass=DnsRClass.IN, ttl=0, rdata=b""):
        if isinstance(rdata, str):
            rdata = bytes(rdata, 'ascii')

        self.name = name

        try:
            self.rtype = DnsRType(rtype)
        except ValueError:
            self.rtype = int(rtype)

        try:
            self.rclass = DnsRClass(rclass)
        except ValueError:
            self.rclass = int(rclass)

        self.ttl = int(ttl)
        self.rdlength = len(rdata) # TODO: drop this?
        self.rdata = rdata

    @classmethod
    def parse(cls, data, offset):
        (name, offset) = DomainName.parse_from(data, offset)
        (rtype, rclass, ttl, rdlength) = struct.unpack_from('!HHIH', data, offset)

        try:
            rtype = DnsRType(rtype)
        except ValueError:
            rtype = int(rtype)
        try:
            rclass = DnsRClass(rclass)
        except ValueError:
            rclass = int(rclass)

        offset += 10

        compressed_rdata = data[offset:offset+rdlength]
        assert len(compressed_rdata) == rdlength

        # Message compression is allowed for the DomainNames in these record types
        # Store normalized data in rdata, and offer compressed_rdata as needed, to reconstruct original packet

        record = cls(name, rtype, rclass, ttl, compressed_rdata)
        if rtype in [DnsRType.NS, DnsRType.SOA, DnsRType.CNAME, DnsRType.PTR]:
            uncompressed_rdata = RData.get_handler(rtype).parse(data, offset).encode()
            if uncompressed_rdata != compressed_rdata:
                record = cls(name, rtype, rclass, ttl, uncompressed_rdata)
                record.compressed_rdata = compressed_rdata #TODO: don't add member variables to classes like this

        offset += rdlength
        return record, offset,

    def __repr__(self):
        return "<Record:%s,%s,%s,%d,%d,%s>" % (
            self.name, self.rtype, self.rclass, self.ttl, self.rdlength, repr(self.rdata))

    def encode(self):
        return self.name.encode() + struct.pack('!HHIH', self.rtype, self.rclass, self.ttl, self.rdlength) + self.rdata


class DnsPacket(object):
    def __init__(self, ID=random.getrandbits(16), QR=DnsQR.query, OPCODE=DnsOpCode.query, AA=False, TC=False, RD=True,
                 RA=True, Z=0, RCODE=DnsResponseCode.no_error, QDCOUNT=None, ANCOUNT=None, NSCOUNT=None, ARCOUNT=None,
                 questions=[], answers=[], nameservers=[], additional_records=[], suffix=bytes()):
        if QDCOUNT is None:
            QDCOUNT = len(questions)
        if ANCOUNT is None:
            ANCOUNT = len(answers)
        if NSCOUNT is None:
            NSCOUNT = len(nameservers)
        if ARCOUNT is None:
            ARCOUNT = len(additional_records)
        if not isinstance(QR, DnsQR):
            QR = DnsQR(QR)
        if not isinstance(OPCODE, DnsOpCode):
            OPCODE = DnsOpCode(OPCODE)
        if not isinstance(RCODE, DnsResponseCode):
            RCODE = DnsResponseCode(RCODE)

        self.ID = int(ID)
        self.QR = QR
        self.OPCODE = OPCODE
        self.AA = bool(AA)
        self.TC = bool(TC)
        self.RD = bool(RD)
        self.RA = bool(RA)
        self.Z = int(Z)
        self.RCODE = RCODE
        self.QDCOUNT = int(QDCOUNT)
        self.ANCOUNT = int(ANCOUNT)
        self.NSCOUNT = int(NSCOUNT)
        self.ARCOUNT = int(ARCOUNT)

        assert isinstance(questions, list)
        self.questions = questions

        assert isinstance(answers, list)
        self.answers = answers

        assert isinstance(nameservers, list)
        self.nameservers = nameservers

        assert isinstance(additional_records, list)
        self.additional_records = additional_records

        assert suffix is None or isinstance(suffix, bytes)
        self.suffix = suffix

    def __repr__(self):
        return "<DnsPacket:%s, questions:%s, answers:%s, nameservers:%s, additional_records: %s>" % (
            hex(self.ID), self.questions, self.answers, self.nameservers, self.additional_records)

    @classmethod
    def parse(cls, data, offset=None):
        # self.datagram = data
        # Transaction ID 16
        (ID,) = struct.unpack_from('!H', data)
        # Query/Response 1
        QR = DnsQR((data[2] & 0b10000000) >> 7)
        # OpCode 4
        OPCODE = DnsOpCode((data[2] & 0b01111000) >> 3)
        # Authoratative Answer 1
        AA = data[2] & 0b100 != 0
        # Truncation 1
        TC = data[2] & 0b10 != 0
        # Recursion Desired 1
        RD = data[2] & 0b1 != 0
        # Recursion Available 1
        RA = data[3] & 0b10000000 != 0
        # Reserved for future, zero value
        Z = (data[3] & 0b01110000) >> 4
        # assert Z == 0 # Newer RFCs obsolete this
        RCODE = DnsResponseCode(data[3] & 0b1111)
        (QDCOUNT, ANCOUNT, NSCOUNT, ARCOUNT,) = struct.unpack_from('!HHHH', data, 4)

        if offset is None:
            offset = 12

        questions = []
        while len(questions) < QDCOUNT:
            (question, offset) = DnsQuestion.parse(data, offset)
            questions.append(question)

        answers = []
        nameservers = []
        additional_records = []
        while offset < len(data):
            (rr, offset) = DnsRecord.parse(data, offset)
            if len(answers) < ANCOUNT:
                answers.append(rr)
            elif len(nameservers) < NSCOUNT:
                nameservers.append(rr)
            elif len(additional_records) < ARCOUNT:
                additional_records.append(rr)
            else:
                raise Exception('Too many/too few records.')
        else:
            assert len(questions) == QDCOUNT
            assert len(answers) == ANCOUNT
            assert len(nameservers) == NSCOUNT
            assert len(additional_records) == ARCOUNT
            cls = (Query if QR == DnsQR.query else Response)
            return (cls(ID, QR, OPCODE, AA, TC, RD, RA, Z, RCODE,
                        QDCOUNT, ANCOUNT, NSCOUNT, ARCOUNT,
                        questions, answers, nameservers, additional_records, suffix=data[offset:]),
                    offset,)

    def encode(self):
        data = struct.pack('!HBBHHHH', self.ID,
                           ((0b10000000 if self.QR == DnsQR.response else 0) |
                            (self.OPCODE << 3) |
                            (0b100 if self.AA else 0) |
                            (0b010 if self.TC else 0) |
                            (0b001 if self.RD else 0)),
                           ((0b10000000 if self.RA else 0) |
                            self.Z << 4 |
                            self.RCODE),
                           self.QDCOUNT,
                           self.ANCOUNT,
                           self.NSCOUNT,
                           self.ARCOUNT,
        )

        for record in self.questions:
            data += record.encode()

        for record in self.answers:
            data += record.encode()

        for record in self.nameservers:
            data += record.encode()

        for record in self.additional_records:
            data += record.encode()

        return data


class Query(DnsPacket):
    pass


class Response(DnsPacket):
    pass


class DomainName(list):
    # TODO: support preservation of the over-the-wire encoding (the raw bytes)
    def __hash__(self):
        return hash(str(self).upper())

    def __eq__(self, other):
        assert isinstance(other, DomainName)
        return hash(self) == hash(other)

    def __ne__(self, other):
        assert isinstance(other, DomainName)
        return hash(self) != hash(other)

    def __str__(self):
        return '.'.join(self)

    def __repr__(self):
        return str(self)

    def __init__(self, labels):
        if labels == []:
            # TODO: auto upgrade [] into the root_label? may be better to force compliance elsewhere
            super(DomainName, self).__init__([''])
        else:
            super(DomainName, self).__init__(labels)

    def enumerate_hierarchy(self):
        yield root_label
        for n in range(len(self)):
            yield DomainName(self[-(n + 1):])

    @staticmethod
    def from_string(name):
        labels = name.rstrip('.').split('.')
        return DomainName(labels)

    @classmethod
    def parse(cls, data):
        return cls.parse_from(data)[0]

    @staticmethod
    def parse_from(data, offset=0):
        starting_offset = offset
        allowed_charset = set(string.ascii_letters + string.digits + '-')
        sequence = []

        while (data[offset]):
            if data[offset] < 64:
                label = data[offset + 1:offset + 1 + data[offset]].decode('ascii')
                assert allowed_charset.issuperset(label)
                offset += data[offset] + 1
                sequence.append(label)
            elif data[offset] >= 0b11000000:
                # A pointer is two bytes
                ptr = ((data[offset] & 0b00111111) << 8) | data[offset + 1]
                offset += 2
                # #TODO: shouldn't allow pointing to 'same label offset' either?
                assert data[ptr] < 64  # Don't allow pointers to pointers
                (label, n,) = DomainName.parse_from(data, ptr)  # RECURSE
                sequence.extend(label)
                break
            else:
                raise Exception('Unknown/Invalid DNS Label')

            assert (sum(map(len, sequence)) < 256)
            # TODO: The limit should actually include the label-length bytes

        else:
            offset += 1  # consume the null terminator

        # TODO: make a proper DomainNameParser/constructor to handle this
        domain_name = DomainName(sequence)
        domain_name.compressed_name = data[starting_offset:offset]

        return domain_name, offset,

    def encode(self):
        # TODO: label name compression, with context of current packet_buffer?
        data = bytearray()
        for label in self:
            if label:
                data.append(len(label))
                data.extend(bytes(label, 'ascii'))
            else:
                break  # ?
        data.append(0)
        return bytes(data)


root_label = DomainName.from_string('.')
