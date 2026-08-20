/* usbreset.c - classic public-domain tool. Sends USBDEVFS_RESET to a USB
 * device node, forcing a real bus-level reset (distinct from driver
 * unbind/rebind, which never touches the electrical/protocol layer).
 * Used by cec-fixup.sh to recover a wedged Pulse-Eight CEC adapter
 * firmware (confirmed 2026-07-30: driver unbind/rebind failed 8 rounds in
 * a row but a single usbreset call recovered it immediately). */
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <sys/ioctl.h>
#include <linux/usbdevice_fs.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s /dev/bus/usb/BBB/DDD\n", argv[0]);
        return 1;
    }
    int fd = open(argv[1], O_WRONLY);
    if (fd < 0) {
        fprintf(stderr, "open %s: %s\n", argv[1], strerror(errno));
        return 1;
    }
    printf("Resetting %s ... ", argv[1]);
    int rc = ioctl(fd, USBDEVFS_RESET, 0);
    if (rc < 0) {
        printf("failed: %s\n", strerror(errno));
        close(fd);
        return 1;
    }
    printf("done\n");
    close(fd);
    return 0;
}
