// Off-device integration harness for pinned upstream MCUboot. No device access.
// Use the same trial/permanent fixtures as upstream sim/tests/core.rs, but target
// only STM32F4 and an explicit alignment (the upstream CLI --align parser fails).
use bootsim::{DeviceName, ImageManipulation, ImagesBuilder, NO_DEPS};

fn board(align: usize) -> ImagesBuilder {
    ImagesBuilder::new(DeviceName::Stm32f4, align, 0xff).expect("STM32F4 simulator configuration")
}

fn main() {
    assert!(std::env::var_os("MCUBOOT_SKIP_SLOW_TESTS").is_none());
    for align in [1, 4, 8] {
        let bad = board(align).make_bad_secondary_slot_image(ImageManipulation::BadSignature);
        assert!(!bad.run_signfail_upgrade(), "bad signature accepted");
        println!("alignment {align}: bad signature rejected");

        // Critical: interrupted revert requires a TRIAL image, not permanent=true.
        let trial = board(align).make_image(&NO_DEPS, false);
        assert!(!trial.run_revert_with_fails(), "interrupted trial revert failed");
        println!("alignment {align}: interrupted trial revert passed");

        let permanent = board(align).make_image(&NO_DEPS, true);
        assert!(!permanent.run_perm_with_fails(), "interrupted permanent update failed");
        assert!(!permanent.run_perm_with_random_fails(5), "repeated interrupted update failed");
        println!("alignment {align}: interrupted permanent update passed");
    }
    println!("STM32F4 targeted simulator checks passed");
}
