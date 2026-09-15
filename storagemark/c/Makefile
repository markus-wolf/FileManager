CC      = cc
# gnu11, not c11: strict c11 makes glibc hide lstat and strnlen, which
# GCC 14 turns into build errors.
CFLAGS  = -O2 -Wall -Wextra -std=gnu11
TARGET  = storagescanner
SRCS    = storagescanner.c hashset.c
OBJS    = $(SRCS:.c=.o)

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET)
