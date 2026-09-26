ifneq ($(KERNELRELEASE),)
obj-m += btmtk.o
else
KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
	rm -rf tests/__pycache__ tests/*.o tests/test_retry_logic

test:
	python3 -m unittest discover -s tests -v

.PHONY: all clean test
endif
