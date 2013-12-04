# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
#!/usr/bin/env python
# Copyright (c) 2013 Christian Schwede <info@cschwede.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

""" swift-ring-tool can be used to migrate a cluster to a new ring with
    an increased partition power and minimal downtime. """

import array
import copy
import optparse
import os
import cPickle as pickle
import sqlite3
import sys
import xattr

from swift.common.ring import Ring
from swift.account.backend import AccountBroker
from swift.container.backend import ContainerBroker


def ring_shift_power(ring):
    """ Returns ring with partition power increased by one. 
   
    Devices will be assigned to partitions like this:

    OLD: 0, 3, 7, 5, 2, 1, ...
    NEW: 0, 0, 3, 3, 7, 7, 5, 5, 2, 2, 1, 1, ...

    Objects have to be moved when using this ring. Please see README.md """


    new_replica2part2dev = []
    for replica in ring['_replica2part2dev']:
        new_replica = array.array('H')
        for device in replica:
            new_replica.append(device)
            new_replica.append(device)  # append device a second time
        new_replica2part2dev.append(new_replica)
    ring['_replica2part2dev'] = new_replica2part2dev
    
    for device in ring['devs']:
        if device:
            device['parts'] *= 2
    
    new_last_part_moves = []
    for partition in ring['_last_part_moves']:
        new_last_part_moves.append(partition)
        new_last_part_moves.append(partition)
    ring['_last_part_moves'] = new_last_part_moves
    
    ring['part_power'] += 1
    ring['parts'] *= 2
    
    return ring


def ring_reset_partitions(ring):
    """ Takes an existing ring and removes device mapping.

    Afterwards a rebalance is required to assign partitions to devices. """

    for dev in ring['devs']:
        if dev:
            dev['parts'] = 0

    ring['_replica2part2dev'] = None 
    ring['_last_part_moves'] = None
    ring['_last_part_moves_epoch'] = None
    ring['devs_changed'] = True
    ring['version'] = 1

    return ring


def get_acc_cont_obj(filename):
    """ Returns account, container, object from XFS object metadata """

    obj_fd = open(filename)
    metadata = ''
    key = 0 
    try:
        while True:
            metadata += xattr.getxattr(obj_fd,
                '%s%s' % ("user.swift.metadata", (key or '')))
            key += 1
    except IOError:
        pass
    obj_fd.close()
    object_name = pickle.loads(metadata).get('name')
    account = object_name.split('/')[1]
    container = object_name.split('/')[2]
    obj = '/'.join(object_name.split('/')[3:])

    return (account, container, obj)


def find_all_files(ringfile, path, options):
    """ Walks filesystem and prints move commands """
    path_elements = len(path.strip('/').split('/'))

    ring = Ring(ringfile)
    for root, _dirs, files in os.walk(path):
        if not "quarantined" in root:
            for filename in files:
                oldname = os.path.join(root, filename)
                path = '/'.join(root.split('/')[:path_elements+2])
                if (options.objects is True and 
                    oldname.split('.')[-1] in ["data", "ts"]):

                    acc, cont, obj = get_acc_cont_obj(oldname)
                    new_part, _nodes = ring.get_nodes(acc, cont, obj)

                    oldname_parts = oldname.split('/')
                    part_pos = oldname_parts.index('objects')
                    oldname_parts[part_pos+1] = str(new_part)
                    newname = '/'.join(oldname_parts)
                    newdir = '/'.join(oldname_parts[:-1])

                    print "#%s/%s/%s" % (acc, cont, obj)
                    print "mkdir -p %s" % newdir
                    print "mv %s %s" % (oldname, newname)
                    print

                if (options.containers is True and 
                    oldname.split('.')[-1] in ["db"] and
                    "containers" in oldname):

                    brkr = ContainerBroker(oldname)
                    info = brkr.get_info()
                    acc = info['account']
                    cont = info['container']

                    new_part, _nodes = ring.get_nodes(acc, cont)

                    oldname_parts = oldname.split('/')
                    part_pos = oldname_parts.index('containers')
                    oldname_parts[part_pos+1] = str(new_part)
                    newname = '/'.join(oldname_parts)
                    newdir = '/'.join(oldname_parts[:-1])

                    print "#%s/%s" % (acc, cont)
                    print "mkdir -p %s" % newdir
                    print "mv %s %s" % (oldname, newname)
                    print

                if (options.accounts is True and 
                    oldname.split('.')[-1] in ["db"] and
                    "accounts" in oldname):

                    brkr = AccountBroker(oldname)
                    info = brkr.get_info()
                    acc = info['account']

                    new_part, _nodes = ring.get_nodes(acc)

                    oldname_parts = oldname.split('/')
                    part_pos = oldname_parts.index('accounts')
                    oldname_parts[part_pos+1] = str(new_part)
                    newname = '/'.join(oldname_parts)
                    newdir = '/'.join(oldname_parts[:-1])

                    print "#%s" % (acc, )
                    print "mkdir -p %s" % newdir
                    print "mv %s %s" % (oldname, newname)
                    print


def main():
    """ Main method... """

    parser = optparse.OptionParser()
    parser.add_option('-r', '--reset', action='store_true')
    parser.add_option('-i', '--increase', action='store_true')
    parser.add_option('-s', '--show', action='store_true')
    parser.add_option('-o', '--objects', action='store_true')
    parser.add_option('-c', '--containers', action='store_true')
    parser.add_option('-a', '--accounts', action='store_true')

    (options, args) = parser.parse_args()

    if options.reset:
        with open(args[0]) as src_ring_fd:
            with open(args[1], "wb") as dst_ring_fd:
                src_ring = pickle.load(src_ring_fd)
                dst_ring = ring_reset_partitions(src_ring)
                pickle.dump(dst_ring, dst_ring_fd, protocol=2)
 
    elif options.increase:
        with open(args[0]) as src_ring_fd:
            with open(args[1], "wb") as dst_ring_fd:
                src_ring = pickle.load(src_ring_fd)
                dst_ring = ring_shift_power(src_ring)
                pickle.dump(dst_ring, dst_ring_fd, protocol=2)
   
    elif options.show:
        with open(args[0]) as src_ring_fd:
            src_ring = pickle.load(src_ring_fd)
            print "Replica:\t0 1 2"
            print "-" * 25
            for part in range(src_ring['parts']):
                devices = []
                for replica in src_ring['_replica2part2dev']:
                    device = replica[part]
                    devices.append(str(device))
                print "Partition %d:\t%s" % (part, ' '.join(devices))

    elif options.objects:
        ringfile = args[0]
        path = args[1]
        find_all_files(ringfile, path, options)

    elif options.containers:
        ringfile = args[0]
        path = args[1]
        find_all_files(ringfile, path, options)
 
    elif options.accounts:
        ringfile = args[0]
        path = args[1]
        find_all_files(ringfile, path, options)
 
    else:
        print "Usage: %s [-r|--reset] <inputfile> <outputfile>" % sys.argv[0]
        print "Usage: %s [-i|--increase] <inputfile> <outputfile>" % sys.argv[0]
        print "Usage: %s [-s|--show] <inputfile>" % sys.argv[0]
        print "Usage: %s [-o|--objects] <ringfile> <path>" % sys.argv[0]
        print "Usage: %s [-c|--containers] <ringfile> <path>" % sys.argv[0]
        print "Usage: %s [-a|--accounts] <ringfile> <path>" % sys.argv[0]


if __name__ == "__main__":
    main()