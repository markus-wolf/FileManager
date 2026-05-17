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

static int   opt_one_fs   = 0;
static int   opt_follow   = 0;
static int   opt_max_depth = 0;   /* 0 = unlimited */
static char *opt_skips[64];
static int   opt_skip_count = 0;

static dev_t root_dev;

/* ------------------------------------------------------------------ */
/* JSON helpers (minimal, no external deps)                             */
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

static void emit(const char *path, const char *name, char type,
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
    else                            type = 'o'; /* other */

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
        /* emit a synthetic error record for the dir */
        char errpath[4096];
        snprintf(errpath, sizeof(errpath), "%s", path);
        emit(errpath, name, 'd', 0, 0,
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
        "  -d <depth>  max depth (0 = unlimited)\n"
        "  --skip <g>  colon-separated glob patterns to skip\n"
        , prog);
    exit(1);
}

int main(int argc, char **argv) {
    char *root = NULL;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-x") == 0)         opt_one_fs = 1;
        else if (strcmp(argv[i], "-L") == 0)    opt_follow = 1;
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

    /* Resolve root device for -x */
    struct stat rst;
    if (stat(root, &rst) != 0) {
        fprintf(stderr, "Cannot stat root: %s\n", strerror(errno));
        return 1;
    }
    root_dev = rst.st_dev;

    inodes = hs_create(65536);
    if (!inodes) { fprintf(stderr, "Out of memory\n"); return 1; }

    /* Emit a small header comment so Python can detect format */
    printf("{\"_storagemark\":1,\"root\":\"%s\"}\n", root);

    walk(root, root, 0);

    hs_destroy(inodes);
    return 0;
}
