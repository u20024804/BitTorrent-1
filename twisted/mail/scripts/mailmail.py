
# Copyright (c) 2001-2004 Twisted Matrix Laboratories.
# See LICENSE for details.

#

"""
Implementation module for the `newtexaco` command.

The name is preliminary and subject to change.
"""

import os
import sys
import rfc822
import socket
import getpass
from ConfigParser import ConfigParser

try:
    import cStringIO as StringIO
except:
    import StringIO

from twisted.internet import reactor
from twisted.mail import bounce, smtp

GLOBAL_CFG = "/etc/mailmail"
LOCAL_CFG = os.path.expanduser("~/.twisted/mailmail")
SMARTHOST = '127.0.0.1'

ERROR_FMT = """\
Subject: Failed Message Delivery

  Message delivery failed.  The following occurred:
  
  %s
-- 
The Twisted sendmail application.
"""

def log(message, *args):
    sys.stderr.write(str(message) % args + '\n')

class Options:
    """
    @type to: C{list} of C{str}
    @ivar to: The addresses to which to deliver this message.
    
    @type sender: C{str}
    @ivar sender: The address from which this message is being sent.
    
    @type body: C{file}
    @ivar body: The object from which the message is to be read.
    """

def getlogin():
    try:
        return os.getlogin()
    except:
        return getpass.getuser()
        

def parseOptions(argv):
    o = Options()
    o.to = [e for e in argv if not e.startswith('-')]
    o.sender = getlogin()
    
    # Just be very stupid

    # Skip -bm -- it is the default
    
    # -bp lists queue information.  Screw that.
    if '-bp' in argv:
        raise ValueError, "Unsupported option"
    
    # -bs makes sendmail use stdin/stdout as its transport.  Screw that.
    if '-bs' in argv:
        raise ValueError, "Unsupported option"
    
    # -F sets who the mail is from, but is overridable by the From header
    if '-F' in argv:
        o.sender = argv[argv.index('-F') + 1]
        o.to.remove(o.sender)
    
    # -i and -oi makes us ignore lone "."
    if ('-i' in argv) or ('-oi' in argv):
        raise ValueError, "Unsupported option"

    # -odb is background delivery
    if '-odb' in argv:
        o.background = True
    else:
        o.background = False
    
    # -odf is foreground delivery
    if '-odf' in argv:
        o.background = False
    else:
        o.background = True
    
    # -oem and -em cause errors to be mailed back to the sender.
    # It is also the default.
    
    # -oep and -ep cause errors to be printed to stderr
    if ('-oep' in argv) or ('-ep' in argv):
        o.printErrors = True
    else:
        o.printErrors = False
    
    # -om causes a copy of the message to be sent to the sender if the sender
    # appears in an alias expansion.  We do not support aliases.
    if '-om' in argv:
        raise ValueError, "Unsupported option"

    # -t causes us to pick the recipients of the message from the To, Cc, and Bcc
    # headers, and to remove the Bcc header if present.
    if '-t' in argv:
        o.recipientsFromHeaders = True
        o.excludeAddresses = o.to
        o.to = []
    else:
        o.recipientsFromHeaders = False
        o.exludeAddresses = []
    
    requiredHeaders = {
        'from': [],
        'to': [],
        'cc': [],
        'bcc': [],
        'date': [],
    }
    
    headers = []
    buffer = StringIO.StringIO()
    while 1:
        write = 1
        line = sys.stdin.readline()
        if not line.strip():
            break
        
        hdrs = line.split(': ', 1)
        
        hdr = hdrs[0].lower()
        if o.recipientsFromHeaders and hdr in ('to', 'cc', 'bcc'):
            o.to.extend([
                a[1] for a in rfc822.AddressList(hdrs[1]).addresslist
            ])
            if hdr == 'bcc':
                write = 0
        elif hdr == 'from':
            o.sender = rfc822.parseaddr(hdrs[1])[1]
        
        if hdr in requiredHeaders:
            requiredHeaders[hdr].append(hdrs[1])

        if write:
            buffer.write(line)

    if not requiredHeaders['from']:
        buffer.write('From: %s\r\n' % (o.sender,))
    if not requiredHeaders['to']:
        if not o.to:
            raise ValueError, "No recipients specified"
        buffer.write('To: %s\r\n' % (', '.join(o.to),))
    if not requiredHeaders['date']:
        buffer.write('Date: %s\r\n' % (smtp.rfc822date(),))

    buffer.write(line)

    if o.recipientsFromHeaders:
        for a in o.excludeAddresses:
            try:
                o.to.remove(a)
            except:
                pass

    buffer.seek(0, 0)
    o.body = StringIO.StringIO(buffer.getvalue() + sys.stdin.read())
    return o

class Configuration:
    """
    @ivar allowUIDs: A list of UIDs which are allowed to send mail.
    @ivar allowGIDs: A list of GIDs which are allowed to send mail.
    @ivar denyUIDs: A list of UIDs which are not allowed to send mail.
    @ivar denyGIDs: A list of GIDs which are not allowed to send mail.

    @type defaultAccess: C{bool}
    @ivar defaultAccess: C{True} if access will be allowed when no other access
    control rule matches or C{False} if it will be denied in that case.

    @ivar useraccess: Either C{'allow'} to check C{allowUID} first
    or C{'deny'} to check C{denyUID} first.

    @ivar groupaccess: Either C{'allow'} to check C{allowGID} first or
    C{'deny'} to check C{denyGID} first.

    @ivar identities: A C{dict} mapping hostnames to credentials to use when
    sending mail to that host.

    @ivar smarthost: C{None} or a hostname through which all outgoing mail will
    be sent.

    @ivar domain: C{None} or the hostname with which to identify ourselves when
    connecting to an MTA.
    """
    def __init__(self):
        self.allowUIDs = []
        self.denyUIDs = []
        self.allowGIDs = []
        self.denyGIDs = []
        self.useraccess = 'deny'
        self.groupaccess= 'deny'

        self.identities = {}
        self.smarthost = None
        self.domain = None

        self.defaultAccess = True


def loadConfig(path):
    # [useraccess]
    # allow=uid1,uid2,...
    # deny=uid1,uid2,...
    # order=allow,deny
    # [groupaccess]
    # allow=gid1,gid2,...
    # deny=gid1,gid2,...
    # order=deny,allow
    # [identity]
    # host1=username:password
    # host2=username:password
    # [addresses]
    # smarthost=a.b.c.d
    # default_domain=x.y.z
    
    c = Configuration()
    
    if not os.access(path, os.R_OK):
        return c

    p = ConfigParser()
    p.read(path)
    
    au = c.allowUIDs
    du = c.denyUIDs
    ag = c.allowGIDs
    dg = c.denyGIDs
    for (section, a, d) in (('useraccess', au, du), ('groupaccess', ag, dg)):
        if p.has_section(section):
            for (mode, L) in (('allow', a), ('deny', d)):
                if p.has_option(section, mode) and p.get(section, mode):
                    for id in p.get(section, mode).split(','):
                        try:
                            id = int(id)
                        except ValueError:
                            log("Illegal %sID in [%s] section: %s", section[0].upper(), section, id)
                        else:
                            L.append(id)
            order = p.get(section, 'order')
            order = map(str.split, map(str.lower, order.split(',')))
            if order[0] == 'allow':
                setattr(c, section, 'allow')
            else:
                setattr(c, section, 'deny')

    if p.has_section('identity'):
        for (host, up) in p.items('identity'):
            parts = up.split(':', 1)
            if len(parts) != 2:
                log("Illegal entry in [identity] section: %s", up)
                continue
            p.identities[host] = parts

    if p.has_section('addresses'):
        if p.has_option('addresses', 'smarthost'):
            c.smarthost = p.get('addresses', 'smarthost')
        if p.has_option('addresses', 'default_domain'):
            c.domain = p.get('addresses', 'default_domain')

    return c

def success(result):
    reactor.stop()

failed = None
def failure(f):
    global failed
    reactor.stop()
    failed = f

def sendmail(host, options, ident):
    d = smtp.sendmail(host, options.sender, options.to, options.body)
    d.addCallbacks(success, failure)
    reactor.run()

def senderror(failure, options):
    recipient = [options.sender]
    sender = '"Internally Generated Message (%s)"<postmaster@%s>' % (sys.argv[0], smtp.DNSNAME)
    error = StringIO.StringIO()
    failure.printTraceback(file=error)
    body = StringIO.StringIO(ERROR_FMT % error.getvalue())

    d = smtp.sendmail('localhost', sender, recipient, body)
    d.addBoth(lambda _: reactor.stop())

def deny(conf):
    uid = os.getuid()
    gid = os.getgid()
    
    if conf.useraccess == 'deny':
        if uid in conf.denyUIDs:
            return True
        if uid in conf.allowUIDs:
            return False
    else:
        if uid in conf.allowUIDs:
            return False
        if uid in conf.denyUIDs:
            return True

    if conf.groupaccess == 'deny':
        if gid in conf.denyGIDs:
            return True
        if gid in conf.allowGIDs:
            return False
    else:
        if gid in conf.allowGIDs:
            return False
        if gid in conf.denyGIDs:
            return True
    
    return not conf.defaultAccess

def run():
    o = parseOptions(sys.argv[1:])
    gConf = loadConfig(GLOBAL_CFG)
    lConf = loadConfig(LOCAL_CFG)
    
    if deny(gConf) or deny(lConf):
        log("Permission denied")
        return

    host = lConf.smarthost or gConf.smarthost or SMARTHOST
    
    ident = gConf.identities.copy()
    ident.update(lConf.identities)
    
    if lConf.domain:
        smtp.DNSNAME = lConf.domain
    elif gConf.domain:
        smtp.DNSNAME = gConf.domain

    sendmail(host, o, ident)

    if failed:
        if o.printErrors:
            failed.printTraceback(file=sys.stderr)
            raise SystemExit(1)
        else:
            senderror(failed, o)
