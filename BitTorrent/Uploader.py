# Written by Bram Cohen
# see LICENSE.txt for license information

from time import time

true = 1
false = 0

class Upload:
    def __init__(self, connection, choker, storage, 
            max_slice_length, max_rate_period, total_up, fudge):
        self.connection = connection
        self.choker = choker
        self.storage = storage
        self.max_slice_length = max_slice_length
        self.max_rate_period = max_rate_period
        self.total_up = total_up
        self.choked = true
        self.interested = false
        self.buffer = []
        self.ratesince = time() - fudge
        self.lastout = self.ratesince
        self.rate = 0.0
        if storage.do_I_have_anything():
            connection.send_bitfield(storage.get_have_list())

    def got_not_interested(self):
        if self.interested:
            self.interested = false
            del self.buffer[:]
            self.choker.not_interested(self.connection)

    def got_interested(self):
        if not self.interested:
            self.interested = true
            self.choker.interested(self.connection)

    def update_rate(self, amount):
        self.total_up[0] += amount
        t = time()
        self.rate = (self.rate * (self.lastout - self.ratesince) + 
            amount) / (t - self.ratesince)
        self.lastout = t
        if self.ratesince < t - self.max_rate_period:
            self.ratesince = t - self.max_rate_period

    def flushed(self):
        while len(self.buffer) > 0 and self.connection.is_flushed():
            index, begin, length = self.buffer[0]
            del self.buffer[0]
            piece = self.storage.get_piece(index, begin, length)
            if piece is None:
                self.connection.close()
                return
            self.update_rate(len(piece))
            self.connection.send_piece(index, begin, piece)

    def got_request(self, index, begin, length):
        if not self.interested or length > self.max_slice_length:
            self.connection.close()
            return
        if not self.choked:
            self.buffer.append((index, begin, length))
            self.flushed()

    def got_cancel(self, index, begin, length):
        try:
            self.buffer.remove((index, begin, length))
        except ValueError:
            pass

    def choke(self):
        if not self.choked:
            self.choked = true
            del self.buffer[:]
            self.connection.send_choke()
        
    def unchoke(self):
        if self.choked:
            self.choked = false
            self.connection.send_unchoke()
        
    def is_choked(self):
        return self.choked
        
    def is_interested(self):
        return self.interested

class DummyConnection:
    def __init__(self, events):
        self.events = events
        self.flushed = false

    def send_bitfield(self, bitfield):
        self.events.append(('bitfield', bitfield))
    
    def is_flushed(self):
        return self.flushed

    def close(self):
        self.events.append('closed')

    def send_piece(self, index, begin, piece):
        self.events.append(('piece', index, begin, piece))

    def send_choke(self):
        self.events.append('choke')

    def send_unchoke(self):
        self.events.append('unchoke')

class DummyChoker:
    def __init__(self, events):
        self.events = events

    def interested(self, connection):
        self.events.append('interested')
    
    def not_interested(self, connection):
        self.events.append('not interested')

class DummyStorage:
    def __init__(self, events):
        self.events = events

    def do_I_have_anything(self):
        self.events.append('do I have')
        return true

    def get_have_list(self):
        self.events.append('get have list')
        return [false, true]

    def get_piece(self, index, begin, length):
        self.events.append(('get piece', index, begin, length))
        if length == 4:
            return None
        return 'a' * length

def test_skip_over_choke():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    assert u.is_choked()
    assert not u.is_interested()
    u.got_interested()
    assert u.is_interested()
    u.got_request(0, 0, 3)
    dco.flushed = true
    u.flushed()
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'interested']

def test_bad_piece():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    assert u.is_choked()
    assert not u.is_interested()
    u.got_interested()
    assert u.is_interested()
    u.unchoke()
    assert not u.is_choked()
    u.got_request(0, 0, 4)
    dco.flushed = true
    u.flushed()
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'interested', 'unchoke', 
        ('get piece', 0, 0, 4), 'closed']

def test_still_rejected_after_unchoke():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    assert u.is_choked()
    assert not u.is_interested()
    u.got_interested()
    assert u.is_interested()
    u.unchoke()
    assert not u.is_choked()
    u.got_request(0, 0, 3)
    u.choke()
    u.unchoke()
    dco.flushed = true
    u.flushed()
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'interested', 'unchoke', 
        'choke', 'unchoke']

def test_sends_when_flushed():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    u.unchoke()
    u.got_interested()
    u.got_request(0, 1, 3)
    dco.flushed = true
    u.flushed()
    u.flushed()
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'unchoke', 'interested', 
        ('get piece', 0, 1, 3), ('piece', 0, 1, 'aaa')]

def test_sends_immediately():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    u.unchoke()
    u.got_interested()
    dco.flushed = true
    u.got_request(0, 1, 3)
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'unchoke', 'interested', 
        ('get piece', 0, 1, 3), ('piece', 0, 1, 'aaa')]

def test_cancel():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    u.unchoke()
    u.got_interested()
    u.got_request(0, 1, 3)
    u.got_cancel(0, 1, 3)
    u.got_cancel(0, 1, 2)
    u.flushed()
    dco.flushed = true
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'unchoke', 'interested']

def test_clears_on_not_interested():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    u.unchoke()
    u.got_interested()
    u.got_request(0, 1, 3)
    u.got_not_interested()
    dco.flushed = true
    u.flushed()
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'unchoke', 'interested', 
        'not interested']

def test_close_when_sends_on_not_interested():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    u.got_request(0, 1, 3)
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'closed']

def test_close_over_max_length():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    u.got_interested()
    u.got_request(0, 1, 101)
    assert events == ['do I have', 'get have list', 
        ('bitfield', [false, true]), 'interested', 'closed']

def test_no_bitfield_on_start_empty():
    events = []
    dco = DummyConnection(events)
    dch = DummyChoker(events)
    ds = DummyStorage(events)
    ds.do_I_have_anything = lambda: false
    u = Upload(dco, dch, ds, 100, 20, [0], 5.0)
    assert events == []
