# csdisplay: Coreshell's display layer for frontends, and csd_test (static, ARM, musl: tools/musl/build-toolchain.sh in test_bench)
#   make            csd_test, ARM build for the MiSTer (CC=/opt/musl-armhf/bin/arm-musl-gcc)
#   make CC=gcc     a host build (it compiles; there's no MiSTer framebuffer to open)
# Frontends add csdisplay.c and csdisplay.h to their own build.
ifeq ($(origin CC),default)
CC = /opt/musl-armhf/bin/arm-musl-gcc
endif
CFLAGS += -O2 -std=gnu11 -Wall
ifeq ($(CC),/opt/musl-armhf/bin/arm-musl-gcc)
CFLAGS += -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard
LDFLAGS += -static -s
endif
csd_test: csd_test.c csdisplay.c csdisplay.h
	$(CC) $(CFLAGS) -o $@ csd_test.c csdisplay.c $(LDFLAGS)
clean:
	rm -f csd_test
