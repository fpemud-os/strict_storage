#!/usr/bin/env python3

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


from .util import Util, BcacheUtil, LvmUtil, EfiCacheGroup
from .handy import CommonChecks, MountEfi, HandyCg, HandyBcache, HandyUtil
from . import errors
from . import StorageLayout


class StorageLayoutImpl(StorageLayout):
    """Layout:
           /dev/sda                 SSD, GPT (cache-disk)
               /dev/sda1            ESP partition
               /dev/sda2            swap device
               /dev/sda3            bcache cache device
           /dev/sdb                 Non-SSD, GPT
               /dev/sdb1            reserved ESP partition
               /dev/sdb2            bcache backing device
           /dev/sdc                 Non-SSD, GPT
               /dev/sdc1            reserved ESP partition
               /dev/sdc2            bcache backing device
           /dev/bcache0             corresponds to /dev/sdb2, LVM-PV for VG hdd
           /dev/bcache1             corresponds to /dev/sdc2, LVM-PV for VG hdd
           /dev/mapper/hdd.root     root device, EXT4
       Description:
           1. /dev/sda1 and /dev/sd{b,c}1 must has the same size
           2. /dev/sda1, /dev/sda2 and /dev/sda3 is order-sensitive, no extra partition is allowed
           3. /dev/sd{b,c}1 and /dev/sd{b,c}2 is order-sensitive, no extra partition is allowed
           4. cache-disk is optional, and only one cache-disk is allowed at most
           5. cache-disk can have no swap partition, /dev/sda2 would be the cache device then
           6. extra LVM-LV is allowed to exist
           7. extra harddisk is allowed to exist
    """

    def __init__(self):
        self._cg = None                     # EfiCacheGroup
        self._mnt = None                    # MountEfi

    @property
    def boot_mode(self):
        return StorageLayout.BOOT_MODE_EFI

    @property
    def dev_rootfs(self):
        return LvmUtil.rootLvDevPath

    @property
    @EfiCacheGroup.proxy
    def dev_boot(self):
        pass

    @property
    @EfiCacheGroup.proxy
    def dev_swap(self):
        pass

    @property
    @EfiCacheGroup.proxy
    def boot_disk(self):
        pass

    def umount_and_dispose(self):
        if True:
            self._mnt.umount()
            del self._mnt
        if True:
            # FIXME: stop and unregister bcache
            del self._cg

    @MountEfi.proxy
    def remount_rootfs(self, mount_options):
        pass

    @MountEfi.proxy
    def get_bootdir_rw_controller(self):
        pass

    def check(self):
        CommonChecks.storageLayoutCheckSwapSize(self)

    def optimize_rootdev(self):
        LvmUtil.autoExtendLv(LvmUtil.rootLvDevPath)
        Util.cmdExec("/sbin/resize2fs", LvmUtil.rootLvDevPath)

    @EfiCacheGroup.proxy
    def get_esp(self):
        pass

    @EfiCacheGroup.proxy
    def get_pending_esp_list(self):
        pass

    @EfiCacheGroup.proxy
    def sync_esp(self, dst):
        pass

    @EfiCacheGroup.proxy
    def get_disk_list(self):
        pass

    @EfiCacheGroup.proxy
    def get_ssd(self):
        pass

    @EfiCacheGroup.proxy
    def get_ssd_esp_partition(self):
        pass

    @EfiCacheGroup.proxy
    def get_ssd_swap_partition(self):
        pass

    @EfiCacheGroup.proxy
    def get_ssd_cache_partition(self):
        pass

    @EfiCacheGroup.proxy
    def get_hdd_list(self):
        pass

    @EfiCacheGroup.proxy
    def get_hdd_esp_partition(self, disk):
        pass

    @EfiCacheGroup.proxy
    def get_hdd_data_partition(self, disk):
        pass

    def add_disk(self, disk):
        assert disk is not None

        if disk not in Util.getDevPathListForFixedDisk():
            raise errors.StorageLayoutAddDiskError(disk, errors.NOT_DISK)

        lastBootHdd = self._cg.boot_disk

        if Util.isBlkDevSsdOrHdd(disk):
            self._cg.add_ssd(disk)

            # ssd partition 3: make it as cache device
            parti = self._cg.get_ssd_cache_partition()
            bcacheDevPathList = [BcacheUtil.findByBackingDevice(self._cg.get_hdd_data_partition(x)) for x in self._cg.get_hdd_list()]
            BcacheUtil.makeAndRegisterCacheDevice(parti)
            BcacheUtil.attachCacheDevice(bcacheDevPathList, parti)
        else:
            self._cg.add_hdd(disk)

            # hdd partition 2: make it as backing device, create lvm physical volume on bcache device and add it to volume group
            parti = self._cg.get_ssd_cache_partition()
            BcacheUtil.makeAndRegisterBackingDevice(parti)
            bcacheDev = BcacheUtil.findByBackingDevice(parti)
            if self.cg.get_ssd() is not None:
                BcacheUtil.attachCacheDevice([bcacheDev], self._cg.get_ssd_cache_partition())
            LvmUtil.addPvToVg(bcacheDev, LvmUtil.vgName)

        # return True means boot disk is changed
        return lastBootHdd != self._cg.boot_disk

    def remove_disk(self, disk):
        assert disk is not None

        lastBootHdd = self._cg.boot_disk

        if self._cg.get_ssd() is not None and disk == self._cg.get_ssd():
            # check if swap is in use
            if self._cg.get_ssd_swap_partition() is not None:
                if Util.systemdFindSwapService(self._cg.get_ssd_swap_partition()) is not None:
                    raise errors.StorageLayoutRemoveDiskError(errors.SWAP_IS_IN_USE)

            # ssd partition 3: remove from cache
            BcacheUtil.unregisterCacheDevice(self._cg.get_ssd_cache_partition())

            # remove
            self._cg.remove_ssd()
        elif disk in self._cg.get_hdd_list():
            # check for last hdd
            if len(self._cg.get_hdd_list()) <= 1:
                raise errors.StorageLayoutRemoveDiskError(errors.CAN_NOT_REMOVE_LAST_HDD)

            # hdd partition 2: remove from volume group
            bcacheDev = BcacheUtil.findByBackingDevice(self._cg.get_hdd_data_partition(disk))
            rc, out = Util.cmdCallWithRetCode("/sbin/lvm", "pvmove", bcacheDev)
            if rc != 5:
                raise errors.StorageLayoutRemoveDiskError("failed")
            Util.cmdCall("/sbin/lvm", "vgreduce", LvmUtil.vgName, bcacheDev)
            BcacheUtil.stopBackingDevice(bcacheDev)

            # remove
            self._cg.remove_hdd(disk)
        else:
            assert False

        return lastBootHdd != self._cg.boot_disk     # boot disk may change


def parse(boot_dev, root_dev):
    if boot_dev is None:
        raise errors.StorageLayoutParseError(StorageLayoutImpl.name, errors.BOOT_DEV_NOT_EXIST)
    if root_dev != LvmUtil.rootLvDevPath:
        raise errors.StorageLayoutParseError(StorageLayoutImpl.name, errors.ROOT_DEV_MUST_BE(LvmUtil.rootLvDevPath))
    if Util.getBlkDevFsType(LvmUtil.rootLvDevPath) != Util.fsTypeExt4:
        raise errors.StorageLayoutParseError(StorageLayoutImpl.name, errors.ROOT_PARTITION_FS_SHOULD_BE(Util.fsTypeExt4))

    # pv list
    pvDevPathList = HandyUtil.lvmEnsureVgLvAndGetPvList(StorageLayoutImpl.name)
    for pvDevPath in pvDevPathList:
        if BcacheUtil.getBcacheDevFromDevPath(pvDevPath) is None:
            raise errors.StorageLayoutParseError(StorageLayoutImpl.name, "volume group \"%s\" has non-bcache physical volume" % (LvmUtil.vgName))

    # ssd, hdd_list, boot_disk
    ssd, hddList = HandyBcache.getSsdAndHddListFromBcacheDevPathList(pvDevPathList)
    ssdEspParti, ssdSwapParti, ssdCacheParti = HandyCg.checkAndGetSsdPartitions(StorageLayoutImpl.name, ssd)
    bootHdd = HandyCg.checkAndGetBootHddFromBootDev(boot_dev, ssdEspParti, hddList)

    # return
    ret = StorageLayoutImpl()
    ret._cg = EfiCacheGroup(ssd=ssd, ssdEspParti=ssdEspParti, ssdSwapParti=ssdSwapParti, ssdCacheParti=ssdCacheParti, hddList=hddList, bootHdd=bootHdd)
    ret._mnt = MountEfi("/")
    return ret


def detect_and_mount(disk_list, mount_dir):
    BcacheUtil.scanAndRegisterAll()
    LvmUtil.activateAll()

    # pv list
    pvDevPathList = HandyUtil.lvmEnsureVgLvAndGetPvList(StorageLayoutImpl.name)
    for pvDevPath in pvDevPathList:
        if BcacheUtil.getBcacheDevFromDevPath(pvDevPath) is None:
            raise errors.StorageLayoutParseError(StorageLayoutImpl.name, "volume group \"%s\" has non-bcache physical volume" % (LvmUtil.vgName))
    # ssd, hdd_list, boot_disk, boot_device
    ssd, hddList = HandyBcache.getSsdAndHddListFromBcacheDevPathList(pvDevPathList)
    HandyCg.checkExtraDisks(ssd, hddList, disk_list)
    ssdEspParti, ssdSwapParti, ssdCacheParti = HandyCg.checkAndGetSsdPartitions(StorageLayoutImpl.name, ssd)
    bootHdd, bootDev = HandyCg.checkAndGetBootHddAndBootDev(ssdEspParti, hddList)

    # check root lv
    if Util.getBlkDevFsType(LvmUtil.rootLvDevPath) != Util.fsTypeExt4:
        raise errors.StorageLayoutParseError(StorageLayoutImpl.name, errors.ROOT_PARTITION_FS_SHOULD_BE(Util.fsTypeExt4))

    # mount
    MountEfi.mount(LvmUtil.rootLvDevPath, bootDev, mount_dir)

    # return
    ret = StorageLayoutImpl()
    ret._cg = EfiCacheGroup(ssd=ssd, ssdEspParti=ssdEspParti, ssdSwapParti=ssdSwapParti, ssdCacheParti=ssdCacheParti, hddList=hddList, bootHdd=bootHdd)
    ret._mnt = MountEfi(mount_dir)
    return ret


def create_and_mount(disk_list, mount_dir):
    # add disks to cache group
    cg = EfiCacheGroup()
    HandyCg.checkAndAddDisks(cg, *Util.splitSsdAndHddFromFixedDiskDevPathList(disk_list))

    # hdd partition 2: make them as backing device
    bcacheDevPathList = []
    for hdd in cg.get_hdd_list():
        parti = cg.get_hdd_data_partition(hdd)
        BcacheUtil.makeAndRegisterBackingDevice(parti)
        bcacheDevPathList.append(BcacheUtil.findByBackingDevice(parti))

    # ssd partition 3: make it as cache device
    if cg.get_ssd() is not None:
        parti = cg.get_ssd_cache_partition()
        BcacheUtil.makeAndRegisterCacheDevice(parti)
        BcacheUtil.attachCacheDevice(bcacheDevPathList, parti)

    # create pv on bcache device, create vg, create root lv
    for bcacheDevPath in bcacheDevPathList:
        LvmUtil.addPvToVg(bcacheDevPath, LvmUtil.vgName, mayCreate=True)
    LvmUtil.createLvWithDefaultSize(LvmUtil.vgName, LvmUtil.rootLvName)

    # mount
    MountEfi.mount(LvmUtil.rootLvDevPath, cg.dev_boot, mount_dir)

    # return
    ret = StorageLayoutImpl()
    ret._cg = cg
    ret._mnt = MountEfi(mount_dir)
    return ret
