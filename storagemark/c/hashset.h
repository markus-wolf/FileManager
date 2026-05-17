#ifndef HASHSET_H
#define HASHSET_H

#include <stdint.h>
#include <stddef.h>

/* Open-addressing hash set keyed on (dev, inode) pairs.
   Returns the path stored on first insertion, or NULL if the
   key was already present (i.e. this is a hard-link duplicate). */

typedef struct HashEntry {
    uint64_t dev;
    uint64_t inode;
    char     path[4096];
    int      used;
} HashEntry;

typedef struct {
    HashEntry *entries;
    size_t     cap;
    size_t     count;
} HashSet;

HashSet *hs_create(size_t initial_cap);
void     hs_destroy(HashSet *hs);

/* Returns 1 if newly inserted (first occurrence), 0 if duplicate.
   On first insert, stores `path` in out_path (unchanged on dup). */
int hs_insert(HashSet *hs, uint64_t dev, uint64_t inode,
              const char *path, char *out_first_path);

#endif
