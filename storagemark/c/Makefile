CC      = cc
CFLAGS  = -O2 -Wall -Wextra -std=c11
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
