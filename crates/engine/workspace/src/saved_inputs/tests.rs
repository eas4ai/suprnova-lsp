use super::SavedWorkspaceInputs;

#[test]
fn fingerprints_saved_contents_additions_and_removals_without_artifact_noise() {
    let fixture = test_fixture::fixture_crate("//- /src/lib.rs\npub struct Post;\n");
    let root = fixture.path("");
    let artifacts = root.join("owned-compiler");
    std::fs::create_dir_all(&artifacts).unwrap();
    let original = SavedWorkspaceInputs::read(&root, Some(&artifacts)).unwrap();
    std::fs::write(root.join("src/lib.rs"), "pub struct Post;\n").unwrap();
    assert_eq!(SavedWorkspaceInputs::read(&root, Some(&artifacts)).unwrap(), original);
    std::fs::write(artifacts.join("generated.rs"), "compiler output").unwrap();
    assert_eq!(SavedWorkspaceInputs::read(&root, Some(&artifacts)).unwrap(), original);
    for relative in ["src/new.rs", "Cargo.toml", "Cargo.lock", ".cargo/config", ".cargo/config.toml", "rust-toolchain", "rust-toolchain.toml"] {
        let path = root.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, "saved input").unwrap();
        assert_ne!(SavedWorkspaceInputs::read(&root, Some(&artifacts)).unwrap(), original, "{relative}");
        std::fs::remove_file(path).unwrap();
        assert_eq!(SavedWorkspaceInputs::read(&root, Some(&artifacts)).unwrap(), original, "{relative}");
    }
    std::fs::write(root.join("src/lib.rs"), "pub struct Other;\n").unwrap();
    assert_ne!(SavedWorkspaceInputs::read(&root, Some(&artifacts)).unwrap(), original);
}

#[test]
fn ignore_names_apply_inside_the_workspace_and_cargo_config_requires_its_directory() {
    let root = std::path::Path::new("/host/target/application");
    assert!(SavedWorkspaceInputs::is_input(root, &root.join("src/lib.rs")));
    assert!(SavedWorkspaceInputs::is_input(root, &root.join(".cargo/config.toml")));
    assert!(!SavedWorkspaceInputs::is_input(root, &root.join("src/config.toml")));
    for directory in [".git", "target", "node_modules", ".direnv"] {
        assert!(!SavedWorkspaceInputs::is_input(root, &root.join(directory).join("source.rs")));
    }
    assert!(!SavedWorkspaceInputs::is_input(root, std::path::Path::new("/other/source.rs")));
}
