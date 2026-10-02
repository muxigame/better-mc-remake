use minisign_verify::{PublicKey, Signature};
use std::{env, fs};
fn main() {
    let args: Vec<String> = env::args().collect();
    let public = PublicKey::from_base64(&args[1]).expect("Committed updater public key invalid");
    let signature = Signature::decode(&fs::read_to_string(&args[2]).expect("Signature missing"))
        .expect("Updater signature malformed");
    let installer = fs::read(&args[3]).expect("Installer missing");
    public.verify(&installer, &signature, false).expect("Updater signature does not match committed public key");
    let mut tampered = installer;
    tampered[0] ^= 1;
    assert!(public.verify(&tampered, &signature, false).is_err(), "Tampering was accepted");
    println!("Updater signature valid against committed public key; tampered installer rejected");
}
