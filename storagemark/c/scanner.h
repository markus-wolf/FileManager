#ifndef SCANNER_H
#define SCANNER_H

#include <stdint.h>

typedef struct {
    char     path[4096];
    char     name[256];
    char     type;        /* 'f', 'd', 'l' */
    uint64_t size_bytes;
    uint64_t size_disk;   /* blocks_512 * 512 */
    uint64_t inode;
    uint64_t dev;
    uint32_t uid;
    int64_t  mtime;
    int64_t  atime;
    int64_t  ctime;       /* birthtime on macOS */
    uint16_t depth;
    char     hardlink_of[4096]; /* non-empty if deduped */
    char     error[256];
} ScanRecord;

#endif
