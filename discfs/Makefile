# discfs: the drive's disc as a cue/bin for Coreshell (static, ARM, musl: tools/musl/build-toolchain.sh in test_bench)
#   make            ARM build for the MiSTer (CC=/opt/musl-armhf/bin/arm-musl-gcc)
#   make CC=gcc     a host build (compiles; needs a drive and FUSE to run)
ifeq ($(origin CC),default)
CC = /opt/musl-armhf/bin/arm-musl-gcc
endif
CFLAGS += -O2 -std=gnu11 -D_GNU_SOURCE -Wall -Wno-unused-variable
ifeq ($(CC),/opt/musl-armhf/bin/arm-musl-gcc)
CFLAGS += -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -I/opt/musl-armhf/kinclude
LDFLAGS += -static -s
endif
discfs: discfs.c
	$(CC) $(CFLAGS) -o $@ discfs.c $(LDFLAGS)
clean:
	rm -f discfs
