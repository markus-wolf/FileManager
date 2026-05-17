#include "hashset.h"
#include <stdlib.h>
#include <string.h>

HashSet *hs_create(size_t initial_cap) {
    HashSet *hs = calloc(1, sizeof(HashSet));
    if (!hs) return NULL;
    hs->cap = initial_cap < 16 ? 16 : initial_cap;
    hs->entries = calloc(hs->cap, sizeof(HashEntry));
    if (!hs->entries) { free(hs); return NULL; }
    return hs;
}

void hs_destroy(HashSet *hs) {
    if (!hs) return;
    free(hs->entries);
    free(hs);
}

static uint64_t mix(uint64_t dev, uint64_t inode) {
    uint64_t h = dev * 2654435761ULL ^ inode * 2246822519ULL;
    h ^= h >> 17;
    h *= 0xbf58476d1ce4e5b9ULL;
    h ^= h >> 31;
    return h;
}

static void hs_grow(HashSet *hs) {
    size_t new_cap = hs->cap * 2;
    HashEntry *new_entries = calloc(new_cap, sizeof(HashEntry));
    if (!new_entries) return;
    for (size_t i = 0; i < hs->cap; i++) {
        if (!hs->entries[i].used) continue;
        uint64_t idx = mix(hs->entries[i].dev, hs->entries[i].inode) % new_cap;
        while (new_entries[idx].used)
            idx = (idx + 1) % new_cap;
        new_entries[idx] = hs->entries[i];
    }
    free(hs->entries);
    hs->entries = new_entries;
    hs->cap = new_cap;
}

int hs_insert(HashSet *hs, uint64_t dev, uint64_t inode,
              const char *path, char *out_first_path) {
    if (hs->count * 2 >= hs->cap)
        hs_grow(hs);

    uint64_t idx = mix(dev, inode) % hs->cap;
    while (hs->entries[idx].used) {
        if (hs->entries[idx].dev == dev && hs->entries[idx].inode == inode) {
            /* duplicate — copy the first-seen path for caller */
            strncpy(out_first_path, hs->entries[idx].path, 4095);
            out_first_path[4095] = '\0';
            return 0;
        }
        idx = (idx + 1) % hs->cap;
    }
    hs->entries[idx].used  = 1;
    hs->entries[idx].dev   = dev;
    hs->entries[idx].inode = inode;
    strncpy(hs->entries[idx].path, path, 4095);
    hs->entries[idx].path[4095] = '\0';
    hs->count++;
    return 1;
}
