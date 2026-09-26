#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>

#define BIT(nr) (1UL << (nr))

#define BTMTK_FW_DL_MAX_RETRIES 3

enum {
	BTMTK_TX_WAIT_VND_EVT,
	BTMTK_FIRMWARE_LOADED,
	BTMTK_HW_RESET_ACTIVE,
	BTMTK_ISOPKT_OVER_INTR,
	BTMTK_ISOPKT_RUNNING,
};

struct hci_dev {
	const char *name;
};

struct btmtk_data {
	unsigned long flags;
	uint32_t dev_id;
	uint32_t fw_dl_retries;
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
		/* Recover from a MT7925 firmware-download failure up to retry limit. */
		if (dev_id == 0x7925) {
			if (btmtk_data->fw_dl_retries < BTMTK_FW_DL_MAX_RETRIES) {
				btmtk_data->fw_dl_retries++;
				btmtk_reset_sync(hdev, btmtk_data);
			} else {
				/* Limit reached: return error without resetting */
			}
		}
		return fw_err;
	}
	return 0;
}

static void btmtk_usb_setup_complete(struct btmtk_data *btmtk_data)
{
	btmtk_data->fw_dl_retries = 0;
}

static void test_repeated_failures_reset_at_most_max_times(void)
{
	struct hci_dev hdev = { .name = "hci0" };
	struct btmtk_data data = { .flags = 0, .dev_id = 0x7925, .fw_dl_retries = 0, .reset_calls = 0 };
	int ret;

	/* 1st failure: reset requested, counter increments to 1 */
	ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 1);
	assert(data.fw_dl_retries == 1);

	/* 2nd failure: reset requested, counter increments to 2 */
	ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 2);
	assert(data.fw_dl_retries == 2);

	/* 3rd failure: reset requested, counter increments to 3 (MAX reached) */
	ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 3);
	assert(data.fw_dl_retries == 3);

	/* 4th failure: limit reached, NO reset requested, counter stays 3 */
	ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 3);
	assert(data.fw_dl_retries == 3);

	/* 5th failure: still no reset requested */
	ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 3);
	assert(data.fw_dl_retries == 3);

	printf("PASS: test_repeated_failures_reset_at_most_max_times\n");
}

static void test_success_rearms_recovery(void)
{
	struct hci_dev hdev = { .name = "hci0" };
	struct btmtk_data data = { .flags = 0, .dev_id = 0x7925, .fw_dl_retries = 0, .reset_calls = 0 };
	int ret;

	/* Fail twice */
	btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(data.reset_calls == 2);
	assert(data.fw_dl_retries == 2);

	/* Setup succeeds */
	ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, 0);
	assert(ret == 0);
	btmtk_usb_setup_complete(&data);
	assert(data.fw_dl_retries == 0);
	assert(data.reset_calls == 2);

	/* Now failure occurs again: recovery must be re-armed */
	for (int i = 1; i <= BTMTK_FW_DL_MAX_RETRIES; i++) {
		ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
		assert(ret == -110);
		assert(data.reset_calls == 2 + i);
		assert(data.fw_dl_retries == (uint32_t)i);
	}

	/* Subsequent failure stops resetting */
	ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(ret == -110);
	assert(data.reset_calls == 2 + BTMTK_FW_DL_MAX_RETRIES);
	assert(data.fw_dl_retries == BTMTK_FW_DL_MAX_RETRIES);

	printf("PASS: test_success_rearms_recovery\n");
}

static void test_exhaustion_then_success_rearms(void)
{
	struct hci_dev hdev = { .name = "hci0" };
	struct btmtk_data data = { .flags = 0, .dev_id = 0x7925, .fw_dl_retries = 0, .reset_calls = 0 };

	/* Exhaust all retries */
	for (int i = 0; i < BTMTK_FW_DL_MAX_RETRIES + 2; i++) {
		btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	}
	assert(data.reset_calls == BTMTK_FW_DL_MAX_RETRIES);
	assert(data.fw_dl_retries == BTMTK_FW_DL_MAX_RETRIES);

	/* Setup completes successfully, re-arming recovery */
	btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, 0);
	btmtk_usb_setup_complete(&data);
	assert(data.fw_dl_retries == 0);
	assert(data.reset_calls == BTMTK_FW_DL_MAX_RETRIES);

	/* Next failure triggers reset */
	btmtk_usb_setup_fw_handler(&hdev, &data, 0x7925, -110);
	assert(data.reset_calls == BTMTK_FW_DL_MAX_RETRIES + 1);
	assert(data.fw_dl_retries == 1);

	printf("PASS: test_exhaustion_then_success_rearms\n");
}

static void test_other_dev_ids_not_affected(void)
{
	struct hci_dev hdev = { .name = "hci0" };
	struct btmtk_data data = { .flags = 0, .dev_id = 0x7961, .fw_dl_retries = 0, .reset_calls = 0 };

	int ret = btmtk_usb_setup_fw_handler(&hdev, &data, 0x7961, -110);
	assert(ret == -110);
	assert(data.reset_calls == 0);
	assert(data.fw_dl_retries == 0);
	printf("PASS: test_other_dev_ids_not_affected\n");
}

int main(void)
{
	test_repeated_failures_reset_at_most_max_times();
	test_success_rearms_recovery();
	test_exhaustion_then_success_rearms();
	test_other_dev_ids_not_affected();
	printf("All C retry logic unit tests passed successfully.\n");
	return 0;
}
