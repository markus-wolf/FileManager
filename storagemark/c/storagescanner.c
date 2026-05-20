#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>
#include <errno.h>
#include <unistd.h>
#include <fnmatch.h>
#include <time.h>
#include "hashset.h"

/* ------------------------------------------------------------------ */
/* Config                                                               */
/* ------------------------------------------------------------------ */

static int   opt_one_fs    = 0;
static int   opt_follow    = 0;
static int   opt_binary    = 0;
static int   opt_max_depth = 0;   /* 0 = unlimited */
static char *opt_skips[64];
static int   opt_skip_count = 0;

static dev_t root_dev;

/* ------------------------------------------------------------------ */
/* Binary record format                                                 */
/*                                                                      */
/* Stream starts with 8-byte magic: "SMRK\x01\x00\x00\x00"            */
/*                                                                      */
/* Each record:                                                         */
/*   [72-byte fixed header]  little-endian                             */
/*     uint8   type          'f','d','l','o','?'                        */
/*     uint16  depth                                                    */
/*     uint8   flags         (reserved)                                 */
/*     uint32  uid                                                      */
/*     uint64  size_bytes                                               */
/*     uint64  size_disk                                                */
/*     uint64  inode                                                    */
/*     uint64  dev                                                      */
/*     int64   mtime                                                    */
/*     int64   atime                                                    */
/*     int64   ctime                                                    */
/*     uint16  path_len                                                 */
/*     uint16  name_len                                                 */
/*     uint16  hardlink_len                                             */
/*     uint16  error_len                                                */
/*   [variable] path (path_len bytes, no NUL)                          */
/*   [variable] name (name_len bytes, no NUL)                          */
/*   [variable] hardlink_of (hardlink_len bytes, no NUL)               */
/*   [variable] error (error_len bytes, no NUL)                        */
/*                                                                      */
/* Python struct format: '<BHBIQQQQqqqHHHH'  (72 bytes)               */
/* ------------------------------------------------------------------ */

#define MAGIC "SMRK\x01\x00\x00\x00"
#define MAGIC_LEN 8
#define HDR_SIZE 72

/* Write exactly n bytes, retrying on short writes */
static void write_all(const void *buf, size_t n) {
    const char *p = (const char *)buf;
    while (n > 0) {
        ssize_t w = write(STDOUT_FILENO, p, n);
        if (w <= 0) break;
        p += w; n -= (size_t)w;
    }
}

static void emit_binary(const char *path, const char *name, char type,
                        uint64_t size_bytes, uint64_t size_disk,
                        uint64_t inode, uint64_t dev, uint32_t uid,
                        int64_t mtime, int64_t atime, int64_t ctime,
                        int depth, const char *hardlink_of, const char *error) {
    uint16_t path_len     = (uint16_t)(path        ? strnlen(path,        0xFFFF) : 0);
    uint16_t name_len     = (uint16_t)(name        ? strnlen(name,        0xFFFF) : 0);
    uint16_t hardlink_len = (uint16_t)(hardlink_of ? strnlen(hardlink_of, 0xFFFF) : 0);
    uint16_t error_len    = (uint16_t)(error       ? strnlen(error,       0xFFFF) : 0);

    /* Build fixed header in a local buffer to avoid multiple write() calls */
    unsigned char hdr[HDR_SIZE];
    memset(hdr, 0, HDR_SIZE);
    size_t o = 0;

    hdr[o++] = (uint8_t)type;                          /* 1 */
    hdr[o++] = (uint8_t)(depth & 0xFF);                /* 2 */
    hdr[o++] = (uint8_t)((depth >> 8) & 0xFF);
    hdr[o++] = 0;                                       /* flags (1) */
    hdr[o++] = (uint8_t)(uid & 0xFF);                  /* uid (4) */
    hdr[o++] = (uint8_t)((uid >>  8) & 0xFF);
    hdr[o++] = (uint8_t)((uid >> 16) & 0xFF);
    hdr[o++] = (uint8_t)((uid >> 24) & 0xFF);

#define WRITE_U64(v) do { \
    uint64_t _v = (uint64_t)(v); \
    for (int _i = 0; _i < 8; _i++) { hdr[o++] = (uint8_t)(_v & 0xFF); _v >>= 8; } \
} while(0)

#define WRITE_I64(v) WRITE_U64((uint64_t)(v))

    WRITE_U64(size_bytes);   /* 8 */
    WRITE_U64(size_disk);    /* 8 */
    WRITE_U64(inode);        /* 8 */
    WRITE_U64(dev);          /* 8 */
    WRITE_I64(mtime);        /* 8 */
    WRITE_I64(atime);        /* 8 */
    WRITE_I64(ctime);        /* 8 */

    hdr[o++] = (uint8_t)(path_len & 0xFF);             /* path_len (2) */
    hdr[o++] = (uint8_t)((path_len >> 8) & 0xFF);
    hdr[o++] = (uint8_t)(name_len & 0xFF);             /* name_len (2) */
    hdr[o++] = (uint8_t)((name_len >> 8) & 0xFF);
    hdr[o++] = (uint8_t)(hardlink_len & 0xFF);         /* hardlink_len (2) */
    hdr[o++] = (uint8_t)((hardlink_len >> 8) & 0xFF);
    hdr[o++] = (uint8_t)(error_len & 0xFF);            /* error_len (2) */
    hdr[o++] = (uint8_t)((error_len >> 8) & 0xFF);

    /* o should be exactly HDR_SIZE */
    write_all(hdr, HDR_SIZE);
    if (path_len)     write_all(path,        path_len);
    if (name_len)     write_all(name,        name_len);
    if (hardlink_len) write_all(hardlink_of, hardlink_len);
    if (error_len)    write_all(error,       error_len);
}

/* ------------------------------------------------------------------ */
/* JSON output (kept for debugging / manual use)                        */
/* ------------------------------------------------------------------ */

static void json_escape(const char *s, char *out, size_t outlen) {
    size_t j = 0;
    for (size_t i = 0; s[i] && j + 6 < outlen; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"')       { out[j++] = '\\'; out[j++] = '"'; }
        else if (c == '\\') { out[j++] = '\\'; out[j++] = '\\'; }
        else if (c == '\n') { out[j++] = '\\'; out[j++] = 'n'; }
        else if (c == '\r') { out[j++] = '\\'; out[j++] = 'r'; }
        else if (c == '\t') { out[j++] = '\\'; out[j++] = 't'; }
        else                { out[j++] = c; }
    }
    out[j] = '\0';
}

static void emit_json(const char *path, const char *name, char type,
                      uint64_t size_bytes, uint64_t size_disk,
                      uint64_t inode, uint64_t dev, uint32_t uid,
                      int64_t mtime, int64_t atime, int64_t ctime,
                      int depth, const char *hardlink_of, const char *error) {
    char epath[4096*2], ename[512], ehardlink[4096*2], eerror[512];
    json_escape(path,        epath,     sizeof(epath));
    json_escape(name,        ename,     sizeof(ename));
    json_escape(hardlink_of, ehardlink, sizeof(ehardlink));
    json_escape(error,       eerror,    sizeof(eerror));

    printf("{\"path\":\"%s\",\"name\":\"%s\",\"type\":\"%c\","
           "\"size_bytes\":%llu,\"size_disk\":%llu,"
           "\"inode\":%llu,\"dev\":%llu,\"uid\":%u,"
           "\"mtime\":%lld,\"atime\":%lld,\"ctime\":%lld,"
           "\"depth\":%d,\"hardlink_of\":\"%s\",\"error\":\"%s\"}\n",
           epath, ename, type,
           (unsigned long long)size_bytes, (unsigned long long)size_disk,
           (unsigned long long)inode, (unsigned long long)dev, (unsigned)uid,
           (long long)mtime, (long long)atime, (long long)ctime,
           depth, ehardlink, eerror);
}

static void emit(const char *path, const char *name, char type,
                 uint64_t size_bytes, uint64_t size_disk,
                 uint64_t inode, uint64_t dev, uint32_t uid,
                 int64_t mtime, int64_t atime, int64_t ctime,
                 int depth, const char *hardlink_of, const char *error) {
    if (opt_binary)
        emit_binary(path, name, type, size_bytes, size_disk,
                    inode, dev, uid, mtime, atime, ctime,
                    depth, hardlink_of, error);
    else
        emit_json(path, name, type, size_bytes, size_disk,
                  inode, dev, uid, mtime, atime, ctime,
                  depth, hardlink_of, error);
}

/* ------------------------------------------------------------------ */
/* Skip-pattern check                                                   */
/* ------------------------------------------------------------------ */

static int should_skip(const char *name) {
    for (int i = 0; i < opt_skip_count; i++)
        if (fnmatch(opt_skips[i], name, 0) == 0)
            return 1;
    return 0;
}

/* ------------------------------------------------------------------ */
/* Recursive walker                                                     */
/* ------------------------------------------------------------------ */

static HashSet *inodes;

static void walk(const char *path, const char *name, int depth) {
    struct stat st;
    int rc = opt_follow ? stat(path, &st) : lstat(path, &st);

    if (rc != 0) {
        emit(path, name, '?', 0, 0, 0, 0, 0, 0, 0, 0, depth, "", strerror(errno));
        return;
    }

    /* Filesystem boundary */
    if (opt_one_fs && depth > 0 && st.st_dev != root_dev)
        return;

    char type;
    if      (S_ISREG(st.st_mode))  type = 'f';
    else if (S_ISDIR(st.st_mode))  type = 'd';
    else if (S_ISLNK(st.st_mode))  type = 'l';
    else                            type = 'o';

    uint64_t size_disk = (uint64_t)st.st_blocks * 512ULL;

#ifdef __APPLE__
    int64_t ctime_val = (int64_t)st.st_birthtimespec.tv_sec;
#else
    int64_t ctime_val = (int64_t)st.st_ctime;
#endif

    /* Hard-link dedup (files only) */
    char hardlink_of[4096] = "";
    uint64_t reported_size = (uint64_t)st.st_size;
    if (type == 'f' && st.st_nlink > 1) {
        int first = hs_insert(inodes, (uint64_t)st.st_dev,
                              (uint64_t)st.st_ino, path, hardlink_of);
        if (!first) {
            reported_size = 0;
            size_disk     = 0;
        }
    }

    emit(path, name, type,
         reported_size, size_disk,
         (uint64_t)st.st_ino, (uint64_t)st.st_dev, (uint32_t)st.st_uid,
         (int64_t)st.st_mtime, (int64_t)st.st_atime, ctime_val,
         depth, hardlink_of, "");

    if (type != 'd') return;
    if (opt_max_depth > 0 && depth >= opt_max_depth) return;

    DIR *dir = opendir(path);
    if (!dir) {
        emit(path, name, 'd', 0, 0,
             (uint64_t)st.st_ino, (uint64_t)st.st_dev, (uint32_t)st.st_uid,
             0, 0, 0, depth, "", strerror(errno));
        return;
    }

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
            continue;
        if (should_skip(ent->d_name))
            continue;

        char child[4096];
        int n = snprintf(child, sizeof(child), "%s/%s", path, ent->d_name);
        if (n < 0 || (size_t)n >= sizeof(child))
            continue;

        walk(child, ent->d_name, depth + 1);
    }
    closedir(dir);
}

/* ------------------------------------------------------------------ */
/* main                                                                 */
/* ------------------------------------------------------------------ */

static void usage(const char *prog) {
    fprintf(stderr,
        "Usage: %s [OPTIONS] <path>\n"
        "  -x          do not cross filesystem boundaries\n"
        "  -L          follow symlinks\n"
        "  -b          binary output mode (faster)\n"
        "  -d <depth>  max depth (0 = unlimited)\n"
        "  --skip <g>  colon-separated glob patterns to skip\n"
        , prog);
    exit(1);
}

int main(int argc, char **argv) {
    char *root = NULL;

    for (int i = 1; i < argc; i++) {
        if      (strcmp(argv[i], "-x") == 0)  opt_one_fs = 1;
        else if (strcmp(argv[i], "-L") == 0)  opt_follow = 1;
        else if (strcmp(argv[i], "-b") == 0)  opt_binary = 1;
        else if (strcmp(argv[i], "-d") == 0 && i+1 < argc)
            opt_max_depth = atoi(argv[++i]);
        else if (strcmp(argv[i], "--skip") == 0 && i+1 < argc) {
            char *tok = strtok(argv[++i], ":");
            while (tok && opt_skip_count < 64) {
                opt_skips[opt_skip_count++] = tok;
                tok = strtok(NULL, ":");
            }
        }
        else if (argv[i][0] != '-') root = argv[i];
        else usage(argv[0]);
    }

    if (!root) usage(argv[0]);

    struct stat rst;
    if (stat(root, &rst) != 0) {
        fprintf(stderr, "Cannot stat root: %s\n", strerror(errno));
        return 1;
    }
    root_dev = rst.st_dev;

    inodes = hs_create(65536);
    if (!inodes) { fprintf(stderr, "Out of memory\n"); return 1; }

    if (opt_binary) {
        /* Switch stdout to binary / unbuffered for raw writes */
        fflush(stdout);
        write_all(MAGIC, MAGIC_LEN);
    } else {
        printf("{\"_storagemark\":1,\"root\":\"%s\"}\n", root);
    }

    walk(root, root, 0);

    if (opt_binary) fflush(stdout);
    hs_destroy(inodes);
    return 0;
}
