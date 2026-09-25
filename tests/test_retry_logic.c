#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>

#define BIT(nr) (1UL << (nr))

enum {
	BTMTK_TX_WAIT_VND_EVT,
	BTMTK_FIRMWARE_LOADED,
	BTMTK_HW_RESET_ACTIVE,
	BTMTK_ISOPKT_OVER_INTR,
	BTMTK_ISOPKT_RUNNING,
	BTMTK_FIRMWARE_DL_RETRY,
};

static inline void set_bit(int nr, unsigned long *addr)
{
	*addr |= BIT(nr);
}

static inline void clear_bit(int nr, unsigned long *addr)
{
	*addr &= ~BIT(nr);
}

static inline int test_bit(int nr, const unsigned long *addr)
{
	return (*addr & BIT(nr)) != 0;
}

static inline int test_and_clear_bit(int nr, unsigned long *addr)
{
	int old = test_bit(nr, addr);
	clear_bit(nr, addr);
	return old;
}

struct hci_dev {
	const char *name;
};

struct btmtk_data {
	unsigned long flags;
	uint32_t dev_id;
	int reset_calls;
};

static void btmtk_reset_sync(struct hci_dev *hdev, struct btmtk_data *data)
{
	(void)hdev;
	data->reset_calls++;
}

static int btmtk_usb_setup_fw_handler(struct hci_dev *hdev, struct btmtk_data *btmtk_data,
				      uint32_t dev_id, int fw_err)
{
	if (fw_err < 0) {
		/* Recover from a MT7925 firmware-download timeout. */
		if (dev_id == 0x7925) {
			set_bit(BTMTK_FIRMWARE_DL_RETRY, &btmtk_data->flags);
			btmtk_reset_sync(hdev, btmtk_data);
		}
		return fw_err;
	}
	return 0;
}

static void btmtk_usb_setup_complete(struct btmtk_data *btmtk_data)
{
	test_and_clear_bit(BTMTK_FIRMWARE_DL_RETRY, &btmtk_data->flags);
}

static void test_initial_failure_triggers_reset_and_keeps_retry(void)
{
	struct hci_dev hdev = { .name = "hci0" };
	struct btmtk_data data = { .flags = 0, .reset_calls = 0 };

	int ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 1);
	assert(test_bit(BTMTK_FIRMWARE_DL_RETRY, &data.flags));
	printf("PASS: test_initial_failure_triggers_reset_and_keeps_retry\n");
}

static void test_subsequent_failure_still_triggers_reset(void)
{
	struct hci_dev hdev = { .name = "hci0" };
	struct btmtk_data data = { .flags = 0, .reset_calls = 0 };

	/* First firmware-download failure triggers reset and sets retry flag */
	btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(data.reset_calls == 1);
	assert(test_bit(BTMTK_FIRMWARE_DL_RETRY, &data.flags));

	/* Second firmware-download failure after reset does NOT spend the only retry */
	int ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 2);
	assert(test_bit(BTMTK_FIRMWARE_DL_RETRY, &data.flags));

	/* Third failure still triggers reset */
	ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 3);
	assert(test_bit(BTMTK_FIRMWARE_DL_RETRY, &data.flags));
	printf("PASS: test_subsequent_failure_still_triggers_reset\n");
}

static void test_setup_completion_clears_retry_flag(void)
{
	struct hci_dev hdev = { .name = "hci0" };
	struct btmtk_data data = { .flags = 0, .reset_calls = 0 };

	/* First firmware failure */
	btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(test_bit(BTMTK_FIRMWARE_DL_RETRY, &data.flags));

	/* Next attempt succeeds and setup completes */
	int ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, 0);
	assert(ret == 0);
	btmtk_usb_setup_complete(&data);
	assert(!test_bit(BTMTK_FIRMWARE_DL_RETRY, &data.flags));
	printf("PASS: test_setup_completion_clears_retry_flag\n");
}

static void test_other_dev_ids_not_affected(void)
{
	struct hci_dev hdev = { .name = "hci0" };
	struct btmtk_data data = { .flags = 0, .reset_calls = 0 };

	int ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7961, -110);
	assert(ret == -110);
	assert(data.reset_calls == 0);
	assert(!test_bit(BTMTK_FIRMWARE_DL_RETRY, &data.flags));
	printf("PASS: test_other_dev_ids_not_affected\n");
}

int main(void)
{
	test_initial_failure_triggers_reset_and_keeps_retry();
	test_subsequent_failure_still_triggers_reset();
	test_setup_completion_clears_retry_flag();
	test_other_dev_ids_not_affected();
	printf("All C retry logic unit tests passed successfully.\n");
	return 0;
}
