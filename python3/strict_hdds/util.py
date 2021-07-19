#!/usr/bin/env python3

# strict_hdds.py - strict harddisks
#
# Copyright (c) 2020-2021 Fpemud <fpemud@sina.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.


def cmdCall(cmd, *kargs):
    # call command to execute backstage job
    #
    # scenario 1, process group receives SIGTERM, SIGINT and SIGHUP:
    #   * callee must auto-terminate, and cause no side-effect
    #   * caller must be terminated by signal, not by detecting child-process failure
    # scenario 2, caller receives SIGTERM, SIGINT, SIGHUP:
    #   * caller is terminated by signal, and NOT notify callee
    #   * callee must auto-terminate, and cause no side-effect, after caller is terminated
    # scenario 3, callee receives SIGTERM, SIGINT, SIGHUP:
    #   * caller detects child-process failure and do appopriate treatment

    ret = subprocess.run([cmd] + list(kargs),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True)
    if ret.returncode > 128:
        # for scenario 1, caller's signal handler has the oppotunity to get executed during sleep
        time.sleep(1.0)
    if ret.returncode != 0:
        print(ret.stdout)
        ret.check_returncode()
    return ret.stdout.rstrip()


def cmdCallWithRetCode(cmd, *kargs):
    ret = subprocess.run([cmd] + list(kargs),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True)
    if ret.returncode > 128:
        time.sleep(1.0)
    return (ret.returncode, ret.stdout.rstrip())


def getPhysicalMemorySize():
    with open("/proc/meminfo", "r") as f:
        # We return memory size in GB.
        # Since the memory size shown in /proc/meminfo is always a
        # little less than the real size because various sort of
        # reservation, so we do a "+1"
        m = re.search("^MemTotal:\\s+(\\d+)", f.read())
        return int(m.group(1)) / 1024 / 1024 + 1


def wipeHarddisk(devpath):
    with open(devpath, 'wb') as f:
        f.write(bytearray(1024))


def devPathIsDiskOrPartition(devPath):
    if re.fullmatch("/dev/sd[a-z]", devPath) is not None:
        return True
    if re.fullmatch("(/dev/sd[a-z])([0-9]+)", devPath) is not None:
        return False
    if re.fullmatch("/dev/xvd[a-z]", devPath) is not None:
        return True
    if re.fullmatch("(/dev/xvd[a-z])([0-9]+)", devPath) is not None:
        return False
    if re.fullmatch("/dev/vd[a-z]", devPath) is not None:
        return True
    if re.fullmatch("(/dev/vd[a-z])([0-9]+)", devPath) is not None:
        return False
    if re.fullmatch("/dev/nvme[0-9]+n[0-9]+", devPath) is not None:
        return True
    if re.fullmatch("(/dev/nvme[0-9]+n[0-9]+)p([0-9]+)", devPath) is not None:
        return False
    assert False

@staticmethod
def devPathPartitionToDiskAndPartitionId(partitionDevPath):
    m = re.fullmatch("(/dev/sd[a-z])([0-9]+)", partitionDevPath)
    if m is not None:
        return (m.group(1), int(m.group(2)))
    m = re.fullmatch("(/dev/xvd[a-z])([0-9]+)", partitionDevPath)
    if m is not None:
        return (m.group(1), int(m.group(2)))
    m = re.fullmatch("(/dev/vd[a-z])([0-9]+)", partitionDevPath)
    if m is not None:
        return (m.group(1), int(m.group(2)))
    m = re.fullmatch("(/dev/nvme[0-9]+n[0-9]+)p([0-9]+)", partitionDevPath)
    if m is not None:
        return (m.group(1), int(m.group(2)))
    assert False

@staticmethod
def devPathPartitionToDisk(partitionDevPath):
    return util.devPathPartitionToDiskAndPartitionId(partitionDevPath)[0]

@staticmethod
def devPathDiskToPartition(diskDevPath, partitionId):
    m = re.fullmatch("/dev/sd[a-z]", diskDevPath)
    if m is not None:
        return diskDevPath + str(partitionId)
    m = re.fullmatch("/dev/xvd[a-z]", diskDevPath)
    if m is not None:
        return diskDevPath + str(partitionId)
    m = re.fullmatch("/dev/vd[a-z]", diskDevPath)
    if m is not None:
        return diskDevPath + str(partitionId)
    m = re.fullmatch("/dev/nvme[0-9]+n[0-9]+", diskDevPath)
    if m is not None:
        return diskDevPath + "p" + str(partitionId)
    assert False

@staticmethod
def bcacheMakeDevice(devPath, backingDeviceOrCacheDevice, blockSize=None, bucketSize=None, dataOffset=None):
    assert isinstance(backingDeviceOrCacheDevice, bool)
    assert blockSize is None or (isinstance(blockSize, int) and blockSize > 0)
    assert bucketSize is None or (isinstance(bucketSize, int) and bucketSize > 0)
    assert dataOffset is None or (isinstance(dataOffset, int) and dataOffset > 0)

    #######################################################################
    # code from bcache-tools-1.0.8
    #######################################################################
    # struct cache_sb {
    #     uint64_t        csum;
    #     uint64_t        offset;    /* sector where this sb was written */
    #     uint64_t        version;
    #     uint8_t         magic[16];
    #     uint8_t         uuid[16];
    #     union {
    #         uint8_t     set_uuid[16];
    #         uint64_t    set_magic;
    #     };
    #     uint8_t         label[SB_LABEL_SIZE];
    #     uint64_t        flags;
    #     uint64_t        seq;
    #     uint64_t        pad[8];
    #     union {
    #         struct {
    #             /* Cache devices */
    #             uint64_t    nbuckets;      /* device size */
    #             uint16_t    block_size;    /* sectors */
    #             uint16_t    bucket_size;   /* sectors */
    #             uint16_t    nr_in_set;
    #             uint16_t    nr_this_dev;
    #         };
    #         struct {
    #             /* Backing devices */
    #             uint64_t    data_offset;
    #             /*
    #             * block_size from the cache device section is still used by
    #             * backing devices, so don't add anything here until we fix
    #             * things to not need it for backing devices anymore
    #             */
    #         };
    #     };
    #     uint32_t        last_mount;        /* time_t */
    #     uint16_t        first_bucket;
    #     union {
    #         uint16_t    njournal_buckets;
    #         uint16_t    keys;
    #     };
    #     uint64_t        d[SB_JOURNAL_BUCKETS];    /* journal buckets */
    # };
    bcacheSbFmt = "QQQ16B16B16B32BQQ8QQHHHHIHH"     # without cache_sb.d

    bcacheSbMagic = [0xc6, 0x85, 0x73, 0xf6, 0x4e, 0x1a, 0x45, 0xca,
                        0x82, 0x65, 0xf5, 0x7f, 0x48, 0xba, 0x6d, 0x81]

    if blockSize is None:
        st = os.stat(devPath)
        if stat.S_ISBLK(st.st_mode):
            out = util.cmdCall("/sbin/blockdev", "--getss", devPath)
            blockSize = int(out) // 512
        else:
            blockSize = st.st_blksize // 512

    if bucketSize is None:
        bucketSize = 1024
    if bucketSize < blockSize:
        raise Exception("bucket size (%d) cannot be smaller than block size (%d)", bucketSize, blockSize)

    devUuid = uuid.uuid4()
    setUuid = uuid.uuid4()

    bcacheSb = bytearray(struct.calcsize(bcacheSbFmt))
    offset_content = None
    offset_version = None

    # cache_sb.csum
    p = struct.calcsize("Q")
    offset_content = p

    # cache_sb.offset
    value = 8               # SB_SECTOR
    struct.pack_into("Q", bcacheSb, p, value)
    p += struct.calcsize("Q")

    # cache_sb.version
    if backingDeviceOrCacheDevice:
        value = 1           # BCACHE_SB_VERSION_BDEV
    else:
        value = 0           # BCACHE_SB_VERSION_CDEV
    offset_version = p
    struct.pack_into("Q", bcacheSb, p, value)
    p += struct.calcsize("Q")

    # cache_sb.magic
    struct.pack_into("16B", bcacheSb, p, *bcacheSbMagic)
    p += struct.calcsize("16B")

    # cache_sb.uuid
    struct.pack_into("16B", bcacheSb, p, *devUuid.bytes)
    p += struct.calcsize("16B")

    # cache_sb.set_uuid
    struct.pack_into("16B", bcacheSb, p, *setUuid.bytes)
    p += struct.calcsize("16B")

    # cache_sb.label
    p += struct.calcsize("32B")

    # cache_sb.flags
    if backingDeviceOrCacheDevice:
        value = 0x01                        # CACHE_MODE_WRITEBACK
    else:
        value = 0x00
    struct.pack_into("Q", bcacheSb, p, value)
    p += struct.calcsize("Q")

    # cache_sb.seq
    p += struct.calcsize("Q")

    # cache_sb.pad
    p += struct.calcsize("8Q")

    if backingDeviceOrCacheDevice:
        if dataOffset is not None:
            # modify cache_sb.version
            value = 4                       # BCACHE_SB_VERSION_BDEV_WITH_OFFSET
            struct.pack_into("Q", bcacheSb, offset_version, value)

            # cache_sb.data_offset
            struct.pack_into("Q", bcacheSb, p, dataOffset)
            p += struct.calcsize("Q")
        else:
            # cache_sb.data_offset
            p += struct.calcsize("Q")
    else:
        # cache_sb.nbuckets
        value = util.getBlkDevSize(devPath) // 512 // bucketSize
        if value < 0x80:
            raise Exception("not enough buckets: %d, need %d", value, 0x80)
        struct.pack_into("Q", bcacheSb, p, value)
        p += struct.calcsize("Q")

    # cache_sb.block_size
    struct.pack_into("H", bcacheSb, p, blockSize)
    p += struct.calcsize("H")

    # cache_sb.bucket_size
    struct.pack_into("H", bcacheSb, p, bucketSize)
    p += struct.calcsize("H")

    # cache_sb.nr_in_set
    if not backingDeviceOrCacheDevice:
        value = 1
        struct.pack_into("H", bcacheSb, p, value)
        p += struct.calcsize("H")

    # cache_sb.nr_this_dev
    p += struct.calcsize("H")

    # cache_sb.last_mount
    p += struct.calcsize("I")

    # cache_sb.first_bucket
    value = (23 // bucketSize) + 1
    struct.pack_into("H", bcacheSb, p, value)
    p += struct.calcsize("H")

    # cache_sb.csum
    crc64 = crcmod.predefined.Crc("crc-64-we")
    crc64.update(bcacheSb[offset_content:])
    struct.pack_into("Q", bcacheSb, 0, crc64.crcValue)

    with open(devPath, "r+b") as f:
        f.write(bytearray(8 * 512))
        f.write(bcacheSb)
        f.write(bytearray(256 * 8))         # cacbe_sb.d

    return (devUuid, setUuid)

@staticmethod
def bcacheIsBackingDevice(devPath):
    return util._bcacheIsBackingDeviceOrCachDevice(devPath, True)

@staticmethod
def bcacheIsCacheDevice(devPath):
    return util._bcacheIsBackingDeviceOrCachDevice(devPath, False)

@staticmethod
def _bcacheIsBackingDeviceOrCachDevice(devPath, backingDeviceOrCacheDevice):
    # see C struct definition in util.bcacheMakeDevice()
    bcacheSbMagicPreFmt = "QQQ"
    bcacheSbMagicFmt = "16B"
    bcacheSbVersionPreFmt = "QQ"
    bcacheSbVersionFmt = "Q"

    bcacheSbMagic = [0xc6, 0x85, 0x73, 0xf6, 0x4e, 0x1a, 0x45, 0xca,
                        0x82, 0x65, 0xf5, 0x7f, 0x48, 0xba, 0x6d, 0x81]
    if backingDeviceOrCacheDevice:
        versionValueList = [
            1,           # BCACHE_SB_VERSION_BDEV
            4,           # BCACHE_SB_VERSION_BDEV_WITH_OFFSET
        ]
    else:
        versionValueList = [
            0,           # BCACHE_SB_VERSION_CDEV
            3,           # BCACHE_SB_VERSION_CDEV_WITH_UUID
        ]

    with open(devPath, "rb") as f:
        f.seek(8 * 512 + struct.calcsize(bcacheSbMagicPreFmt))
        buf = f.read(struct.calcsize(bcacheSbMagicFmt))
        if list(buf) != bcacheSbMagic:
            return False

        f.seek(8 * 512 + struct.calcsize(bcacheSbVersionPreFmt))
        buf = f.read(struct.calcsize(bcacheSbVersionFmt))
        value = struct.unpack(bcacheSbVersionFmt, buf)[0]
        if value not in versionValueList:
            return False

        return True

@staticmethod
def bcacheGetSetUuid(devPath):
    # see C struct definition in util.bcacheMakeDevice()
    bcacheSbSetUuidPreFmt = "QQQ16B16B"
    bcacheSbSetUuidFmt = "16B"

    assert util.bcacheIsCacheDevice(devPath)

    with open(devPath, "rb") as f:
        f.seek(8 * 512 + struct.calcsize(bcacheSbSetUuidPreFmt))
        buf = f.read(struct.calcsize(bcacheSbSetUuidFmt))
        return uuid.UUID(bytes=buf)

@staticmethod
def bcacheGetSlaveDevPathList(bcacheDevPath):
    """Last element in the returned list is the backing device, others are cache device"""

    retList = []

    slavePath = "/sys/block/" + os.path.basename(bcacheDevPath) + "/slaves"
    for slaveDev in os.listdir(slavePath):
        retList.append(os.path.join("/dev", slaveDev))

    bcachePath = os.path.realpath("/sys/block/" + os.path.basename(bcacheDevPath) + "/bcache")
    backingDev = os.path.basename(os.path.dirname(bcachePath))
    backingDevPath = os.path.join("/dev", backingDev)

    retList.remove(backingDevPath)
    retList.append(backingDevPath)
    return retList

@staticmethod
def bcacheFindByBackingDevice(devPath):
    for fn in glob.glob("/dev/bcache*"):
        if re.fullmatch("/dev/bcache[0-9]+", fn):
            bcachePath = os.path.realpath("/sys/block/" + os.path.basename(devPath) + "/bcache")
            backingDev = os.path.basename(os.path.dirname(bcachePath))
            if os.path.basename(devPath) == backingDev:
                return fn
    return None

@staticmethod
def isBlkDevSsdOrHdd(devPath):
    bn = os.path.basename(devPath)
    with open("/sys/block/%s/queue/rotational" % (bn), "r") as f:
        buf = f.read().strip("\n")
        if buf == "1":
            return False
    return True

@staticmethod
def getBlkDevSize(devPath):
    out = util.cmdCall("/sbin/blockdev", "--getsz", devPath)
    return int(out) * 512        # unit is byte

@staticmethod
def getBlkDevPartitionTableType(devPath):
    if not util.devPathIsDiskOrPartition(devPath):
        devPath = util.devPathPartitionToDisk(devPath)

    ret = util.cmdCall("/sbin/blkid", "-o", "export", devPath)
    m = re.search("^PTTYPE=(\\S+)$", ret, re.M)
    if m is not None:
        return m.group(1)
    else:
        return ""

@staticmethod
def getBlkDevFsType(devPath):
    ret = util.cmdCall("/sbin/blkid", "-o", "export", devPath)
    m = re.search("^TYPE=(\\S+)$", ret, re.M)
    if m is not None:
        return m.group(1).lower()
    else:
        return ""

@staticmethod
def getBlkDevLvmInfo(devPath):
    """Returns (vg-name, lv-name)
        Returns None if the device is not lvm"""

    rc, ret = util.cmdCallWithRetCode("/sbin/dmsetup", "info", devPath)
    if rc == 0:
        m = re.search("^Name: *(\\S+)$", ret, re.M)
        assert m is not None
        return m.group(1).split(".")
    else:
        return None

@staticmethod
def gptNewGuid(guidStr):
    assert len(guidStr) == 36
    assert guidStr[8] == "-" and guidStr[13] == "-" and guidStr[18] == "-" and guidStr[23] == "-"

    # struct gpt_guid {
    #     uint32_t   time_low;
    #     uint16_t   time_mid;
    #     uint16_t   time_hi_and_version;
    #     uint8_t    clock_seq_hi;
    #     uint8_t    clock_seq_low;
    #     uint8_t    node[6];
    # };
    gptGuidFmt = "IHHBB6s"
    assert struct.calcsize(gptGuidFmt) == 16

    guidStr = guidStr.replace("-", "")

    # really obscure behavior of python3
    # see http://stackoverflow.com/questions/1463306/how-does-exec-work-with-locals
    ldict = {}
    exec("n1 = 0x" + guidStr[0:8], globals(), ldict)
    exec("n2 = 0x" + guidStr[8:12], globals(), ldict)
    exec("n3 = 0x" + guidStr[12:16], globals(), ldict)
    exec("n4 = 0x" + guidStr[16:18], globals(), ldict)
    exec("n5 = 0x" + guidStr[18:20], globals(), ldict)
    exec("n6 = bytearray()", globals(), ldict)
    for i in range(0, 6):
        exec("n6.append(0x" + guidStr[20 + i * 2:20 + (i + 1) * 2] + ")", globals(), ldict)

    return struct.pack(gptGuidFmt, ldict["n1"], ldict["n2"], ldict["n3"], ldict["n4"], ldict["n5"], ldict["n6"])

@staticmethod
def gptIsEspPartition(devPath):
    # struct mbr_partition_record {
    #     uint8_t  boot_indicator;
    #     uint8_t  start_head;
    #     uint8_t  start_sector;
    #     uint8_t  start_track;
    #     uint8_t  os_type;
    #     uint8_t  end_head;
    #     uint8_t  end_sector;
    #     uint8_t  end_track;
    #     uint32_t starting_lba;
    #     uint32_t size_in_lba;
    # };
    mbrPartitionRecordFmt = "8BII"
    assert struct.calcsize(mbrPartitionRecordFmt) == 16

    # struct mbr_header {
    #     uint8_t                     boot_code[440];
    #     uint32_t                    unique_mbr_signature;
    #     uint16_t                    unknown;
    #     struct mbr_partition_record partition_record[4];
    #     uint16_t                    signature;
    # };
    mbrHeaderFmt = "440sIH%dsH" % (struct.calcsize(mbrPartitionRecordFmt) * 4)
    assert struct.calcsize(mbrHeaderFmt) == 512

    # struct gpt_entry {
    #     struct gpt_guid type;
    #     struct gpt_guid partition_guid;
    #     uint64_t        lba_start;
    #     uint64_t        lba_end;
    #     uint64_t        attrs;
    #     uint16_t        name[GPT_PART_NAME_LEN];
    # };
    gptEntryFmt = "16s16sQQQ36H"
    assert struct.calcsize(gptEntryFmt) == 128

    # struct gpt_header {
    #     uint64_t            signature;
    #     uint32_t            revision;
    #     uint32_t            size;
    #     uint32_t            crc32;
    #     uint32_t            reserved1;
    #     uint64_t            my_lba;
    #     uint64_t            alternative_lba;
    #     uint64_t            first_usable_lba;
    #     uint64_t            last_usable_lba;
    #     struct gpt_guid     disk_guid;
    #     uint64_t            partition_entry_lba;
    #     uint32_t            npartition_entries;
    #     uint32_t            sizeof_partition_entry;
    #     uint32_t            partition_entry_array_crc32;
    #     uint8_t             reserved2[512 - 92];
    # };
    gptHeaderFmt = "QIIIIQQQQ16sQIII420s"
    assert struct.calcsize(gptHeaderFmt) == 512

    # do checking
    diskDevPath, partId = util.devPathPartitionToDiskAndPartitionId(devPath)
    with open(diskDevPath, "rb") as f:
        # get protective MBR
        mbrHeader = struct.unpack(mbrHeaderFmt, f.read(struct.calcsize(mbrHeaderFmt)))

        # check protective MBR header
        if mbrHeader[4] != 0xAA55:
            return False

        # check protective MBR partition entry
        found = False
        for i in range(0, 4):
            pRec = struct.unpack_from(mbrPartitionRecordFmt, mbrHeader[3], struct.calcsize(mbrPartitionRecordFmt) * i)
            if pRec[4] == 0xEE:
                found = True
        if not found:
            return False

        # get the specified GPT partition entry
        gptHeader = struct.unpack(gptHeaderFmt, f.read(struct.calcsize(gptHeaderFmt)))
        f.seek(gptHeader[10] * 512 + struct.calcsize(gptEntryFmt) * (partId - 1))
        partEntry = struct.unpack(gptEntryFmt, f.read(struct.calcsize(gptEntryFmt)))

        # check partition GUID
        if partEntry[0] != util.gptNewGuid("C12A7328-F81F-11D2-BA4B-00A0C93EC93B"):
            return False

    return True

@staticmethod
def initializeDisk(devPath, partitionTableType, partitionInfoList):
    assert partitionTableType in ["mbr", "gpt"]
    assert len(partitionInfoList) >= 1

    if partitionTableType == "mbr":
        partitionTableType = "msdos"

    def _getFreeRegion(disk):
        region = None
        for r in disk.getFreeSpaceRegions():
            if r.length <= disk.device.optimumAlignment.grainSize:
                continue                                                # ignore alignment gaps
            if region is not None:
                assert False                                            # there should be only one free region
            region = r
        if region.start < 2048:
            region.start = 2048
        return region

    def _addPartition(disk, pType, pStart, pEnd):
        region = parted.Geometry(device=disk.device, start=pStart, end=pEnd)
        if pType == "":
            partition = parted.Partition(disk=disk, type=parted.PARTITION_NORMAL, geometry=region)
        elif pType == "esp":
            assert partitionTableType == "gpt"
            partition = parted.Partition(disk=disk,
                                            type=parted.PARTITION_NORMAL,
                                            fs=parted.FileSystem(type="fat32", geometry=region),
                                            geometry=region)
            partition.setFlag(parted.PARTITION_ESP)     # which also sets flag parted.PARTITION_BOOT
        elif pType == "bcache":
            assert partitionTableType == "gpt"
            partition = parted.Partition(disk=disk, type=parted.PARTITION_NORMAL, geometry=region)
        elif pType == "swap":
            partition = parted.Partition(disk=disk, type=parted.PARTITION_NORMAL, geometry=region)
            if partitionTableType == "mbr":
                partition.setFlag(parted.PARTITION_SWAP)
            elif partitionTableType == "gpt":
                pass            # don't know why, it says gpt partition has no way to setFlag(SWAP)
            else:
                assert False
        elif pType == "lvm":
            partition = parted.Partition(disk=disk, type=parted.PARTITION_NORMAL, geometry=region)
            partition.setFlag(parted.PARTITION_LVM)
        elif pType == "vfat":
            partition = parted.Partition(disk=disk,
                                            type=parted.PARTITION_NORMAL,
                                            fs=parted.FileSystem(type="fat32", geometry=region),
                                            geometry=region)
        elif pType in ["ext2", "ext4", "xfs"]:
            partition = parted.Partition(disk=disk,
                                            type=parted.PARTITION_NORMAL,
                                            fs=parted.FileSystem(type=pType, geometry=region),
                                            geometry=region)
        else:
            assert False
        disk.addPartition(partition=partition,
                            constraint=disk.device.optimalAlignedConstraint)

    def _erasePartitionSignature(devPath, pStart, pEnd):
        # fixme: this implementation is very limited
        with open(devPath, "wb") as f:
            f.seek(pStart * 512)
            if pEnd - pStart + 1 < 32:
                f.write(bytearray((pEnd - pStart + 1) * 512))
            else:
                f.write(bytearray(32 * 512))

    # partitionInfoList => preList & postList
    preList = None
    postList = None
    for i in range(0, len(partitionInfoList)):
        pSize, pType = partitionInfoList[i]
        if pSize == "*":
            assert preList is None
            preList = partitionInfoList[:i]
            postList = partitionInfoList[i:]
    if preList is None:
        preList = partitionInfoList
        postList = []

    # delete all partitions
    disk = parted.freshDisk(parted.getDevice(devPath), partitionTableType)
    disk.commit()

    # process preList
    for pSize, pType in preList:
        region = _getFreeRegion(disk)
        constraint = parted.Constraint(maxGeom=region).intersect(disk.device.optimalAlignedConstraint)
        pStart = constraint.startAlign.alignUp(region, region.start)
        pEnd = constraint.endAlign.alignDown(region, region.end)

        m = re.fullmatch("([0-9]+)(MiB|GiB|TiB)", pSize)
        assert m is not None
        sectorNum = parted.sizeToSectors(int(m.group(1)), m.group(2), disk.device.sectorSize)
        if pEnd < pStart + sectorNum - 1:
            raise Exception("not enough space")

        _addPartition(disk, pType, pStart, pStart + sectorNum - 1)
        _erasePartitionSignature(devPath, pStart, pEnd)

    # process postList
    for pSize, pType in postList:
        region = _getFreeRegion(disk)
        constraint = parted.Constraint(maxGeom=region).intersect(disk.device.optimalAlignedConstraint)
        pStart = constraint.startAlign.alignUp(region, region.start)
        pEnd = constraint.endAlign.alignDown(region, region.end)

        if pSize == "*":
            _addPartition(disk, pType, pStart, pEnd)
            _erasePartitionSignature(devPath, pStart, pEnd)
        else:
            assert False

    disk.commit()
    time.sleep(3)           # FIXME, wait kernel picks the change

@staticmethod
def isBufferAllZero(buf):
    for b in buf:
        if b != 0:
            return False
    return True

@staticmethod
def getDevPathListForFixedHdd():
    ret = []
    for line in util.cmdCall("/bin/lsblk", "-o", "NAME,TYPE", "-n").split("\n"):
        m = re.fullmatch("(\\S+)\\s+(\\S+)", line)
        if m is None:
            continue
        if m.group(2) != "disk":
            continue
        if re.search("/usb[0-9]+/", os.path.realpath("/sys/block/%s/device" % (m.group(1)))) is not None:      # USB device
            continue
        ret.append("/dev/" + m.group(1))
    return ret
